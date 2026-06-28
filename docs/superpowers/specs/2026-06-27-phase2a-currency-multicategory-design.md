# Phase 2A: Currency-family multi-category scanning — design (REVISED for re-review)

Increment A of 2. Makes `/categories` + `/threshold` actually drive scanning for the **17
currency-family categories** (currency, fragments, runes, essences, ultimatum, expedition,
ritual, vaultkeys, breach, abyss, uncutgems, lineagesupportgems, delirium, incursion, idol,
verisium, vaal) — all served by `Currencies/ByCategory?Category=X` with the SAME item shape the
bot already parses. Uniques (different endpoint + junk-price gate) are Increment B, OUT OF SCOPE.

This revision folds in the prior section-review findings. Verified live: currency CurrentPrice
is exalt-based (`exalted`=1.0, `divine`=DivinePrice anchor), so the existing anchor conversion is
correct for all currency-family categories.

## Section 1 — Category registry (bot.py): NO CODE CHANGE
The `CATEGORIES` constant ALREADY contains exactly the 17 currency-family `(api_id, label)`
pairs, and `/categories` autocomplete + `/threshold` choices already offer them. Increment A
introduces NO uniques, so CATEGORIES stays a 2-tuple list — the prior review's tuple-shape
Critical (test unpacks) and the 24/25 choice-cap concern DO NOT APPLY here; they belong to
Increment B. No `family` field, no `category_family` helper yet (YAGNI until B adds a 2nd endpoint).

## Section 2 — Client (poe2scout.py)
- `get_currency_overview(league, category="currency", per_page=250)`: add `category` param;
  build the querystring with `quote(category, safe='')` for defense-in-depth (current 17 ids are
  `[a-z]+`, safe, but match the league-path treatment). Default keeps both existing callers
  (`scheduler.poll_once`, `bot.ItemService.refresh`) byte-identical. Keep `per_page` keyword-only
  at call sites.
- **Request throttle (review Critical):** add a constructor arg `req_delay_s: float = 0.4` and
  `await asyncio.sleep(self._req_delay_s)` BEFORE each HTTP GET in the pagination loop (needs
  `import asyncio`). With ≤17 categories × ~1 page each that bounds a poll to a few seconds and
  respects poe2scout's ~2 req/s etiquette. Tests construct the client with `req_delay_s=0` so the
  suite stays fast. (No `_paginate_by_category` extraction yet — there is still ONE paginating
  method in Increment A; the shared helper lands in B when `get_uniques_overview` is added.)

## Section 3 — Normalizer (normalize.py)
- `normalize_currency(raw, league_id, anchor, src_ts, category="currency")`: add `category` param.
  Tag each Observation with **`obs.category = category`** (the REQUESTED api_id we fetched), NOT
  `CategoryApiId` — this guarantees `obs.category` equals the `thr:<cat>` key Section 5 looks up,
  and is deterministic. Default preserves current callers/tests.
- `_daily_volume`: add `if entry is None: continue` before `entry.get("Quantity")`. Defensive
  (no None entries seen in currency-family, but the fix is correct and free; it also unblocks B).
- Everything else unchanged. `is_currency_pair=True` retained for all currency-family items.

## Section 4 — Poll loop (scheduler.py)
Restructure `poll_once`, preserving the existing ordering invariants:
1. `league` guard, then `get_currency_overview`-independent setup: fetch `get_league_meta`,
   build `anchor` (DivinePrice/ChaosDivinePrice + `_clamp_anchor`), persist anchors, bootstrap
   `league` row + `set_active_league` (early-league mute). ALL of this stays ABOVE and OUTSIDE the
   per-category loop. **A `get_league_meta` failure is systemic** → `record_failure()` (+ notify if
   it trips) + `return -1` (do NOT advance `last_poll_ts`). (review Important.)
2. Read the `categories` setting → list (comma-split, strip, dedupe); default `["currency"]` when
   unset/empty. Drop any id not in `CATEGORIES` with a `log.warning` (no raising).
3. For each category: `try: raw = await client.get_currency_overview(league, cat);
   obs += normalize_currency(raw, league, anchor, now_ts, cat)` wrapped in `except Exception:
   log.warning(...)` → skip that category, continue. Track `succeeded` count.
4. **Aggregate breaker decision (review Critical):** make exactly ONE breaker call AFTER the loop.
   If `succeeded == 0` (every category failed) → `record_failure()` (+ notify `source_down` if it
   trips) + `return -1` (no `last_poll_ts` advance). Else → proceed.
5. Single `detect(store, all_obs, anchor, started, now_ts, cfg, category_floors)` over the union
   (see Section 5 for `category_floors`). Notify kept events + overflow. `set_setting(last_poll_ts)`,
   `record_success()`. (Do NOT call `record_success` until a non-systemic poll completes.)
- item_id uniqueness invariant (stated): currency `ApiId`s are globally unique strings across the
  17 categories, so the `(item_id, src_ts)` PK never collides within a poll.
- Note: `/pollnow`'s "items ingested" (`count_obs_at_latest_poll`) now sums across scanned
  categories — expected, not a regression.

## Section 5 — Detection: per-category cap + per-category thresholds (engine.py + scheduler.py)
**Per-category top-K (user-chosen):** currently `detect` (engine.py:164-166) sorts the whole
candidate union by severity and caps at `cfg.top_k` globally. Change to GROUP candidates by
`obs.category` during the loop (`candidates_by_cat: dict[str, list[AlertEvent]]`, appending under
`obs.category` for both price and demand events), then per category sort-by-severity and cap at
`cfg.top_k`; `kept` = concatenation, `overflow` = sum of per-category overflows. So a volatile
currency poll can't starve fragments/etc. `cfg.top_k` semantics become PER-CATEGORY (kept at 8;
cooldowns + floors keep real counts far lower). Document the semantic change in DetectConfig.

**Per-category thresholds:**
- `poll_once` builds `category_floors: dict[str,float]` = `{cat: float(thr) for cat in scanned if
  (thr := settings "thr:<cat>")}`; pass into `detect` → `evaluate_price`.
- New param `category_floors: dict[str,float] | None = None` on both `detect` and `evaluate_price`
  (default → treat as `{}`), so all existing `test_engine.py` calls keep working unchanged.
- evaluate_price floor restructure (current code is priority if/elif/else, NOT a max):
  ```
  base = (category_floors or {}).get(obs.category, cfg.floor_pct)
  if is_low:                          floor = max(base, cfg.low_liq_floor)   # 0.40 guard
  elif obs.price_exalt < cfg.cheap_price:  floor = max(base, cfg.cheap_floor_pct)  # 0.25 guard
  else:                               floor = base
  ```
  So a per-category threshold sets/raises the baseline but can NEVER weaken the LOW-liq / cheap
  guards. Unset category → `cfg.floor_pct` (0.15) → byte-identical to today.
- **fast_path stays GLOBAL** (decision, documented): `fast = abs(log_move) >= cfg.fast_path_log
  and not is_low` is unchanged; per-category thresholds gate only the statistical/2-of-3 path, not
  the immediate big-move fire. Acceptable; revisit if users want category-scaled fast paths.
- `set_threshold_logic` (bot.py): validate `spike_pct` — reject values outside `0 < x < 5` with a
  hint ("use a fraction, e.g. 0.2 for 20%"), so a `/threshold currency 20` footgun (stores 20.0 →
  category never fires) is caught at entry. (review Minor, now load-bearing.)

## Decisions (locked with user)
- Per-category alert cap (not global). Default `cfg.top_k` (per category).
- Default scan set `["currency"]` when `/categories` unset — exact current behavior.
- fast_path remains global (per-category thresholds don't scale it).
- Tier thresholds (5k/100k daily volume) NOT recalibrated — currency-family volumes are the same
  regime the existing thresholds were calibrated on.
- Throttle 0.4s/request (configurable; 0 in tests).

## Testing intent (TDD)
- get_currency_overview: `category` threads into the querystring (page-aware stub); `req_delay_s=0`
  path; default category="currency" unchanged.
- normalize_currency: `category` param tags `obs.category` = requested id; default still "currency".
- _daily_volume: skips a None PriceLog entry.
- poll_once: scans the configured category set (stub client records requested categories); default
  ["currency"] when unset; one failing category is skipped (others still ingest); ALL-fail →
  record_failure + return -1 + last_poll_ts NOT advanced; meta-failure → return -1. Update existing
  test_scheduler stubs to accept the `category` kwarg.
- detect: per-category cap (two categories each over cap → each capped independently, overflow
  summed); category_floors override raises base floor; LOW-liq still uses 0.40; default None → today.
- set_threshold_logic: rejects out-of-range spike_pct.
