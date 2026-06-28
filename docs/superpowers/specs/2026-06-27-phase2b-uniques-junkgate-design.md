# Phase 2B: Uniques/equipment scanning + junk-price gate — design (for section review)

Increment B of 2. Builds on Phase 2A (currency-family multi-category scanning, shipped). Adds the
7 UNIQUE categories — accessory, armour, flask, jewel, map, weapon, sanctum — served by a DIFFERENT
endpoint (`Uniques/ByCategory`) with a different item shape, plus a junk-price gate to silence the
flood of vendor-tier uniques pinned at the 1-exalt display floor.

## Live API facts (re-verified, league "Runes of Aldur")
- Categories: `Items/Categories` → `CurrencyCategories` (17, Phase 2A) + `UniqueCategories` (7).
- Uniques endpoint `GET /poe2/Leagues/{league}/Uniques/ByCategory?Category=X&PerPage=&Page=` —
  SAME `{CurrentPage,Pages,Total,Items}` envelope as currency. Each item:
  `UniqueItemId`(int), `ItemId`(int), `Text`("Bluetongue Shortsword"), `Name`("Bluetongue"),
  `CategoryApiId`("weapon"), `Type`(base, "Shortsword"), `IsChanceable`(bool),
  `CurrentPrice`(exalt-equiv, VERIFIED same unit as currency), `CurrentQuantity`,
  `PriceLogs:[{Price,Time,Quantity} | null]` (null entries occur). NO `ApiId`.
- Price unit CONFIRMED exalt: cheapest uniques sit at exactly 1.0 ex (huge stock, e.g. Idol of
  Uldurn qty 6448, Blood of the Warrior qty 11537); dear ones are real chase items (Mageblood
  ~562 div, Temporalis ~3416 div). The 1.0-ex floor items are vendor trash with NO price signal —
  a 1→2 ex tick is +100% and would false-alert. This is what the junk gate suppresses.

## Section 1 — Registry: NEW neutral module `poe2bot/categories.py` (REVISED per S4 review)
The registry MUST live in a discord-free module so the scheduler (core) can read it without
importing `bot.py` (which imports discord). Create `poe2bot/categories.py`:
- `CATEGORIES: list[tuple[str,str,str]]` = `(api_id, label, family)`, `family ∈ {"currency","uniques"}`.
  The 17 currency-family entries (moved verbatim from bot.py) gain `"currency"`; append 7 uniques
  with `"uniques"`: ("accessory","Accessories"), ("armour","Armour"), ("flask","Flasks"),
  ("jewel","Jewels"), ("map","Maps"), ("weapon","Weapons"), ("sanctum","Sanctum Research"). Total 24.
- `_FAMILY_BY_ID = {api_id: family for api_id, _, family in CATEGORIES}`.
- `category_family(api_id: str) -> str | None`: `return _FAMILY_BY_ID.get(api_id)` — returns None for
  an unknown id (NO raise), so the poll loop does a single lookup and skips on None (resolves the
  prior cross-section KeyError concern without a separate membership helper).
- `bot.py` does `from .categories import CATEGORIES, category_family` (and keeps `CATEGORIES`
  importable from `poe2bot.bot` via the re-export, so `from poe2bot.bot import CATEGORIES` in tests
  still works). `scheduler.py` does `from .categories import category_family` — no discord in core.
- Update EVERY consumer that unpacks CATEGORIES (prior S1 breakage list):
  - bot.py `filter_category_choices`: `for api_id, label in` → `for api_id, label, _ in`.
  - bot.py `/threshold` `app_commands.choices` builder: `for api_id, label in` → `for api_id, label, _ in`.
  - the doc-comment ("17 here"/2-tuple) moves to categories.py and updates to 24 / 3-tuple / uniques.
  - tests/test_bot_commands.py: `test_categories_constant_shape` (`for api_id, _` and `for a, l`)
    and `test_filter_category_choices` (`for a, _`) → 3-tuple unpack (`for a, *_`, `for a, l, fam`),
    and assert `fam in {"currency","uniques"}`.
- `/threshold` static choices now 24/25 — within the cap; add a comment noting the 1-slot headroom
  (if a future increment exceeds 25, switch `/threshold` to autocomplete like `/categories`).

## Section 2 — Client (poe2scout.py): DRY paginator + uniques method
- Extract the shared pagination into `_paginate_by_category(self, league, segment, category,
  per_page)`: builds `…/Leagues/{quote(league)}/{segment}/ByCategory?Category={quote(category)}
  &PerPage&Page`, runs the existing page loop WITH the `req_delay_s` throttle, returns
  `{"Items":[...],"Pages":n,"Total":t}`.
- `get_currency_overview(league, category="currency", per_page=250)` → one-line wrapper over
  `_paginate_by_category(league, "Currencies", category, per_page)`. Behavior byte-identical.
- New `get_uniques_overview(league, category, per_page=250)` → wrapper over
  `_paginate_by_category(league, "Uniques", category, per_page)`.

## Section 3 — Normalizer (normalize.py): normalize_uniques
New `normalize_uniques(raw, league_id, anchor, src_ts, category)` → list[Observation]. Per item:
- skip if `CurrentPrice` is None or ≤ 0.
- `item_id = f"unique-{it['UniqueItemId']}"` (UniqueItemId is a globally-unique int across the 7
  unique categories; the `unique-` prefix also avoids collision with currency string ApiIds).
- `name = it.get("Text") or it.get("Name") or item_id`.
- `category = category` (the requested api_id, matching `thr:<cat>` — same rule as Phase 2A).
- `is_currency_pair = False`.
- `price = float(it["CurrentPrice"])`; `log_price = to_log_price(price)`; `price_exalt = price`.
- `volume = vol_daily = _daily_volume(it)` (already skips null PriceLog entries — Phase 2A fix).
- `stock = float(it["CurrentQuantity"]) if present else None`.
- `wall_ts = src_ts`, `doi = None`, `liq_tier = tier_from_volume(volume)`, `trade_id = None`,
  `valid = True`. (All required Observation fields present — prior S3 review flagged wall_ts/doi.)

## Section 4 — Poll loop (scheduler.py): dispatch by family
- `from .categories import category_family` (neutral module — no discord in core).
- After parsing `categories`, for each `cat`: `fam = category_family(cat)`; `if fam is None:
  log.warning("unknown category %s", cat); continue` (single lookup, None = unknown → skip). Then:
  - `fam == "currency"` → `get_currency_overview(league, cat)` + `normalize_currency(..., cat)` (today).
  - `fam == "uniques"`  → `get_uniques_overview(league, cat)`  + `normalize_uniques(..., cat)`.
- The fetch stays inside the per-category try/except (a bad fetch is skipped, breaker trips only when
  ALL fail). Everything else (meta-first, succeeded-count breaker aggregation, category_floors, union
  detect, ordering) is UNCHANGED.
- Cost: scanning all 24 ≈ 24 paginated fetches × 0.4s throttle ≈ ~10-15s/poll — fine on a 30-min
  timer; the user only scans what they enable.

## Section 5 — Junk-price gate (engine.py) (REVISED per S5 review)
Suppress alerts for items with no real price signal (the 1-exalt floor flood), WITHOUT dropping the
observation (history still accrues).
- Add `DetectConfig.min_alert_price_exalt: float = 1.0`. `import math` in engine.py.
- In `evaluate_price`, place the gate AFTER the `if not fires: return PriceVerdict(None, None, None)`
  line (alongside the existing cooldown / early_league checks) so it only fires for items that WOULD
  have alerted — NOT for every floor-sitter every poll (avoids suppressed-row write amplification).
  Gate on the BASELINE in log space (no exp needed; handles min≤0 cleanly):
  `if cfg.min_alert_price_exalt > 0 and reference <= math.log(cfg.min_alert_price_exalt):
       return PriceVerdict(None, None, "below_min_price")`.
  Gating on the baseline silences floor-sitters that tick up (a 1→2 ex move has a baseline at the
  floor → reference ≤ log(1.0)=0 → gated). A valuable item (baseline > 1 ex) is unaffected. Because
  it's after `fires`, a non-moving junk item just falls to `fires=False` → cheap pending-reset, no row.
- In `detect`, add `"below_min_price"` to the recorded-reason tuple (currently
  `verdict.reason in ("cooldown", "early_league_mute")`) so the suppression is observable, not silent.
- In `evaluate_demand`: early `if obs.price_exalt <= cfg.min_alert_price_exalt: return None` (a
  worthless item's demand drop isn't "no longer worth farming" news; bounded — returns None, no row).
- Default 1.0 ex = only the exact display floor; currency essentially unaffected (base Exalted Orb
  sits at 1.0 but never moves). Configurable; `min_alert_price_exalt=0` disables the gate.
- Accepted residuals (documented, non-blocking): (a) demand gate uses CURRENT price while price gate
  uses baseline — a floor-sitter momentarily ticked >1 ex with a ≥50% volume drop could still fire
  DEMAND_COLLAPSE (narrow: needs current>1 AND base vol ≥5000 AND ≥50% drop same poll); (b) an item
  first seen ABOVE 1 ex can fast-path fire on its 2nd sighting — but that's above the junk floor by
  definition, so not junk leakage at the default.

## Decisions (flagged)
- Junk gate applies to ALL items (currency + uniques), not uniques-only — simpler, and harmless for
  currency at the 1.0 default. Reviewer to confirm.
- Uniques inherit the existing tiering: thin daily volumes → mostly LOW tier → existing bigger-move +
  2-of-3 + ⚠ caution handling. No recalibration.
- `/threshold` stays a static choices dropdown at 24/25 (not switched to autocomplete yet).

## Testing intent (TDD)
- registry: 24 entries, family tags correct, `category_family` returns the family and **returns
  None on unknown** (poll loop skips); existing CATEGORIES-unpacking tests updated to 3-tuple.
- client: `_paginate_by_category` page loop (page-aware stub); `get_uniques_overview` hits the
  Uniques segment + threads category; `get_currency_overview` unchanged (segment "Currencies").
- normalize_uniques: maps all fields incl. wall_ts/doi/float casts, item_id scheme, skips no-price,
  is_currency_pair False, null PriceLog handled.
- poll loop: a uniques category routes to get_uniques_overview+normalize_uniques (stub records
  endpoint per category); a currency+uniques mix ingests both; unknown category skipped.
- junk gate (default min=1.0): NEW tests — evaluate_price suppresses a baseline-1.0 floor-sitter that
  ticks to 2.0 (reason "below_min_price", no fire, no fast-path) and FIRES for a baseline-5.0 item
  jumping +30%; evaluate_demand returns None for a price-1.0 item and fires for a price-5.0 item;
  detect records the below_min_price suppression. min=0 disables the gate.
- **Fixture migration (REQUIRED — the existing engine fixtures use baseline EXACTLY 1.0 ex, which
  the default gate now suppresses):** the legacy tests in test_engine.py + test_smoke.py exercise
  OTHER behaviors (jumps/crashes/cooldown/caps/demand), so construct their `DetectConfig` with
  `min_alert_price_exalt=0` to disable the junk gate. The dedicated junk-gate tests above use the
  default. (Do NOT claim "behavior unchanged at 1.0" — a baseline-1.0 mover IS now gated by design.)
