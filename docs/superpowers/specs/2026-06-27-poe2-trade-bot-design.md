# PoE2 Trade Price-Swing Discord Bot — Design Spec

**Date:** 2026-06-27
**Status:** Approved design, pre-implementation
**Codename (detector):** MLCS — Median-Ledger Changepoint Sentinel

## 1. Purpose

A Discord bot that monitors the Path of Exile 2 market and alerts when an item the
user cares about **jumps in price/demand**, **crashes**, or — the flagship case —
**stops being worth farming** (demand drying up even while the sticker price still
looks fine). It watches whole item categories plus a global "top movers" feed and
posts to one configured channel.

The guiding principle: **worth-farming is realizable value, not sticker price.** A
high quoted price backed by no buyers is worthless. Therefore the bot weighs price
against liquidity and realized demand, not price alone.

## 2. Locked decisions

| Area | Decision |
|---|---|
| Language/stack | Python 3.11+, `discord.py` (slash commands), `aiohttp`, SQLite via `aiosqlite`, `APScheduler` |
| Hosting | Always-on (Docker + systemd), 24/7 |
| Watch scope | Whole categories + global top-movers feed |
| Delivery | One configured channel; loud out-of-band ops/health channel separately |
| Trigger model | Change-point vs frozen reference + liquidity gate + realized-demand collapse |
| League handling | `/setleague` with **live autocomplete** from the **poe2scout leagues endpoint** (no hardcoding; poe.ninja not involved); `/leagues` lists them |
| Poll interval | Default ~30 min (configurable); windows are time-based so cadence drift is tolerated |
| **Primary data** | **poe2scout** (`api.poe2scout.com`) — load-bearing |
| Cross-check data | poe.ninja PoE2 economy endpoint — optional sanity check only |
| Build phasing | **Collector-first** (see §10) |

### Why poe2scout is primary (not poe.ninja)

Research (verified) found poe.ninja's PoE2 endpoint is *thin*: only
`core{version,timestamp}`, `lines[]{id, primaryValue, volumePrimaryValue,
secondaryValue, volumeSecondaryValue}`, `items[]{id,name,icon,tradeId}`. It has **no
sparkline, no trend, no confidence flag**, and 404s to plain `curl` behind Cloudflare.
poe2scout has a documented OpenAPI, per-item `CurrentPrice`, `PriceHistory{Price,Time,
Quantity}`, `DailyStatsHistory` OHLCV, league anchors, and — uniquely — **realized
trade flow and supply depth** (`ValueTraded`, `VolumeTraded`, `StockValue`,
`HighestStock`, `RelativePrice`) for currency-exchange pairs. Those flow fields are the
*only* true-demand signal in either API and are required for the DEMAND COLLAPSE alert.

Verified corrections that shape the design:
- `volumePrimaryValue` and `listingCount` are **liquidity/supply**, NOT demand (verified).
- poe.ninja `primaryValue` is dual-encoded — ≥1 = cost, <1 = items-per-unit (verified).
  *Design choice* (not a verified fact): resolve the inversion **structurally per market**,
  not by per-value magnitude (which flaps at parity). Moot while poe2scout is primary —
  its `CurrentPrice` is read in its own unit and never inverted; this only touches the
  optional poe.ninja cross-check.
- The "30-minute poe2scout cache" is a third-party client TTL, **not** an API property;
  true refresh cadence is undocumented (~hourly) and must be **measured empirically**.

## 3. Architecture

Each module has one job and a clear interface.

```
main.py            entry: load config, init DB, start bot + scheduler
config.py          env + persisted settings (token, channel ids, poll interval)
sources/
  poe2scout.py     primary client: items, currencies, price/daily history, flow fields
  poeninja.py      optional cross-check client (browser-like headers for Cloudflare)
  leagues.py       fetch available leagues -> [str], cached, daily refresh
  normalize.py     unify into Observation{item_id, log_price, price_exalt, flow, ...}
store.py           SQLite: schema, inserts, windowed reads, pruning, rollups
detector/
  pipeline.py      one poll cycle: fetch -> normalize -> store -> detect -> alert
  signals.py       compute robust price, dispersion, flow deltas, WFS
  changepoint.py   CUSUM / Page-Hinkley / EWMA (Phase 2); simple log-change (Phase 1)
  gating.py        data-quality + liquidity + freshness + early-league gates
  budget.py        fleet FDR / top-K alert budget
alerts.py          format Discord embeds; trade deep-link via items[].tradeId
bot.py             discord client + slash commands
scheduler.py       APScheduler poll loop + nightly prune/rollup
health.py          circuit breaker, pipeline-health channel, dead-man's switch
```

## 4. Data model (SQLite)

One row per item **per distinct server timestamp** (deduped — unchanged snapshots are
dropped so they cannot collapse the dispersion estimate). All prices stored in
log-space and normalized to an Exalted-equivalent via the league anchor.

| Table | Key fields | Purpose | Retention |
|---|---|---|---|
| `league` | league_id PK, ggg_key, display_name, started_at_real, anchor_divine, anchor_chaos, active | Rollover keyed on **stable id**; `league_age` from real start (deflation suppression) distinct from data age | All; raw of inactive pruned |
| `item` | item_id PK, league_id, category, name, tradeId, is_currency_pair, first/last_seen | Route currency-pair (high-cadence flow) vs non-currency (daily demand); TTL eviction | Evict if unseen 14d |
| `obs` | (item_id, src_ts) PK, wall_ts, log_price, price_exalt, vol_realized (**level in P1**; first-differenced in P2), vol_daily, stock_qty, doi, liq_tier, valid, gap, synthetic, dq_flags | Self-derived time-series (substitutes for the missing sparkline); authoritative raw store | 14d raw → daily |
| `daily_rollup` | item_id, day, OHLC, log_mad, vol_sum, stock_med, hour_of_week_profile | Long history, cold-start/rollover seed, diurnal deseasonalization | Last 3 leagues |
| `basket` | poll_id, src_ts, basket_index, members, weights, shock_flags | Currency/market-wide detrending & shock detection (replaces 2-element anchor) | 30d raw → daily |
| `detector_state` | item_id, mu_frozen, cusum_up, cusum_dn, ph_demand, ewma, ewma_var, beta_basket, n_obs, tier, last_fire_up/dn_ts, recovery_count, surprise_score | O(1) recursive scalars; **frozen** change-point reference; full reset on fire/re-baseline/league change. **Phase 1 populates only** mu_frozen, n_obs, tier, last_fire_*_ts, recovery_count; CUSUM/PH/EWMA/beta columns are Phase-2 | One row/item |
| `alert_log` | alert_id, item_id, src_ts, class, direction, magnitude, baseline, current, severity, fired, suppressed_reason | Every fire **and** every suppression with reason (auditable) | Fires whole league; suppressions 7d |
| `source_health` | source, consecutive_failures, last_ok_ts, breaker_open, last_heartbeat_ts | Circuit breaker + pipeline-health; external dead-man watches heartbeat | Current row |

Storage is bounded by construction: 14d raw + daily rollup + last-3-leagues + nightly
prune/VACUUM.

## 5. Poll cycle

Each step is tagged **[P1]** (Phase 1, ships now) or **[P2]** (Phase 2, after history
accrues). The Phase-1 subset is fully self-contained — no [P1] step depends on a [P2]
step. One row per item per **distinct server timestamp** (deduped — unchanged snapshots
are dropped so they cannot collapse the dispersion estimate).

1. **[P1] Fetch** poe2scout (contact User-Agent, rate-limit 2 req/s burst 5); optionally
   poe.ninja cross-check (browser-like headers). Record poll with server timestamp.
2. **[P1] Freshness/dedup gate:** if server timestamp equals last poll's (no refresh) or
   is older than ~3× measured refresh → mark stale; persist raw but **disable firing**
   this cycle (log only).
3. **[P1] Anchors:** read DivinePrice/ChaosDivinePrice; sanity-clamp implausible
   poll-over-poll anchor moves (one bad anchor must not poison every item).
4. **[P1] Normalize per item:** poe2scout `CurrentPrice` read in its own unit and
   converted to Exalted-equiv log-price (**never inverted**). poe.ninja lines
   (cross-check only) resolve dual-encoding structurally per market.
5. **[P1] Liquidity tier** LOW/MED/HIGH from poe2scout depth using **static floors**
   (LOW <100, MED 100–999, HIGH ≥1000 qty — provisional defaults, configurable).
   **[P2]** diurnal adjustment once `hour_of_week_profile` accrues.
6. **[P1] Demand level:** record `VolumeTraded` (currency pairs) or daily `Volume`
   (non-currency items) as a **level**; compute days-of-inventory `doi = stock / volume`.
   Use `VolumeTraded` only; `ValueTraded = volume × price` is price-contaminated.
   **[P2]** first-difference consecutive distinct snapshots into per-interval flow once
   the field's aggregation window is measured (see §13 #1).
7. **[P1] Persist** `obs` row.
8. **[P1] Load baselines** (time-windowed, ~24h price / ~48h demand) and `detector_state`.
9. **[P1] Data-quality meta-gate** (§7) — block all market fires under uncertainty.
10. **[P1] Detectors:** JUMP / CRASH (log-change vs frozen reference) + DEMAND_COLLAPSE
    (volume-**level** relative-drop). **[P2]** CUSUM / Page-Hinkley / DEMAND_SURGE (§6).
11. **[P2] Currency-regime suppressor:** if >30% of items co-move or the basket diverges,
    emit one aggregate alert and suppress constituents.
12. **[P1] Persistence:** 2-of-3 fresh-poll leaky confirmation per class+direction.
13. **[P1] Cooldown + global cap:** per-item per-direction 6h cooldown (opposite-direction
    recovery bypass capped); then a **global per-poll top-K cap** (default K=8) — emit the
    K highest-severity alerts and collapse the rest into one "+N more movers" summary line
    so a correlated burst cannot spam the channel. **[P2]** replace the cap with
    Benjamini-Hochberg FDR across all items.
14. **[P1] Fire:** write `alert_log`, send Discord embed, update `detector_state`
    (freeze new reference on fire).
15. **[P1] Nightly:** prune to retention, roll into `daily_rollup`, reset daily counters,
    refresh `league_age`.

## 6. Detection algorithm

### Phase 1 (ships first — provisional, robust)
Until a replay corpus exists, use a **simple, suppression-first** detector:
- Robust per-poll value = the deduped log-price (temporal, single aggregate per item).
- **Log-change vs a frozen reference** (last confirmed level), AND'd with an absolute
  floor (±15%, ±25% for cheap <2ex / low-dispersion items).
- Hard **liquidity gate** (LOW tier suppresses price alerts), **freshness gate**,
  **2-of-3 persistence**, **6h cooldown**, **early-league 48h crash mute**, and the
  **global per-poll top-K cap** (§5 step 13) so a correlated currency move can't spam.
- DEMAND COLLAPSE: compare the current `VolumeTraded` **level** (currency pairs) or daily
  `Volume` level (non-currency) to a robust baseline (median over the trailing window);
  fire on a sustained ≥50% drop expressed as a robust-z against the demand series' own
  MAD, Poisson/NegBin-gated in thin markets (absolute floor ≥10 trades/day before
  eligible), while price is ~flat and supply is flat/rising. **Phase 1 uses the level, not
  a first-difference** — first-differencing waits until the field's aggregation window is
  measured (a trailing-24h field would make naive diffs tiny, noisy, and sometimes
  negative; §13 #1). Below the trades/day floor the item is ineligible, not fired.
- **Magnitude fast-path:** a single-poll log-move ≥0.40 (≈±50%) with liquidity ≥ MED and
  fresh data fires immediately, bypassing the 2-of-3 persistence wait (still subject to the
  liquidity/freshness gates, cooldown, and global cap). This is what lets a genuine large
  move surface even during baseline warmup.
- Everything logs `suppressed_reason` so behavior is auditable from day one.

The collector simultaneously accumulates the history Phase 2 needs.

### Phase 2 (turns on after history accrues — full MLCS)
- **Two-sided CUSUM in log-space** against the frozen reference: `S_up = max(0, S_up +
  z − k)`, `S_dn = max(0, S_dn − z − k)`, `k=0.5σ`, `h≈5` **calibrated empirically** on
  replayed history (not the i.i.d. ARL formula), inflated by effective-sample-size
  `n_eff = n(1−ρ)/(1+ρ)` for AR(1) autocorrelation; pre-whiten via AR(1) innovations.
- Fire iff CUSUM breaches `h` **OR** single-poll cumulative move ≥0.40 (fast path),
  AND cumulative move vs frozen ref ≥ floor, AND liquidity ≥ MED, AND idiosyncratic
  (basket-detrended), AND persistence, AND survives fleet budget.
- **DEMAND_COLLAPSE** via one-sided **Page-Hinkley** on first-differenced flow against a
  frozen demand reference, scored (not a rigid AND), with explicit **stuck-seller**
  (supply piling) vs **capitulation** (supply falling slower than demand) branches and a
  wash-trade guard. Non-currency items run the same on **daily** Volume (never sub-daily).
- **EWMA-on-log-return** path for MAD≈0 quantized cheap items (drop the EWMA term when σ
  truly collapses — never vacuously true).
- **MAD floored** at a price-quantum-aware epsilon.

## 7. Gating & safety rules (apply before any fire)

- **Data-quality meta-gate:** require fresh + distinct server timestamp, ≥ W/2 real
  samples, gap density <50%, anchor in-bounds, `league_age > 48h` for down-alerts. Else
  log and block.
- **Pipeline-health (loud, separate channel):** >K consecutive stale/failed polls, 4xx/5xx,
  sharp `n_items` drop, or missing heartbeat → ops alert + **external dead-man's switch**.
  "Cannot see the market" is never silently confused with "market is calm."
- **Outage:** write `gap=1, valid=0`, **never a synthetic price** (a dead API must not
  masquerade as a crash); circuit breaker after 5 fails; poe.ninja 404 just drops the
  cross-check.
- **Fleet FDR / top-K budget** per poll bounds aggregate alert fatigue regardless of N.

## 8. Slash commands

| Command | Who | Effect |
|---|---|---|
| `/setleague <name>` | admin | Live **autocomplete** from available leagues; validated against the live list before persisting |
| `/leagues` | any | List currently-available leagues |
| `/categories` | admin | Toggle which categories are scanned |
| `/threshold <category> <...>` | admin | Per-category sensitivity overrides |
| `/topmovers [n]` | any | On-demand current biggest movers by signed magnitude |
| `/price <item>` | any | On-demand current value + liquidity + WFS |
| `/status` | any | League, channel, thresholds, last poll time, source health |
| `/watch` / `/unwatch <item>` | any | Optional per-user item subscription (later phase) |

## 9. Worth-Farming Score (WFS)

**Phase 1 WFS (ships now, no unconfirmed inputs):**
`WFS_p1 = realizable_price_div × absorption_per_hr^0.7`
where `realizable_price_div = (CurrentPrice × liquidity_gate g) / DivineAnchor`
(`g = 0 / 0.6 / 1.0` for LOW/MED/HIGH) and `absorption_per_hr = VolumeTraded_24h / 24`
for currency pairs, or `DailyVolume / 24` for non-currency items. This uses only
confirmed poe2scout fields — no depth-VWAP, no `assumed_supply_per_hr` constant. The
concavity (`^0.7`) stops pure high-churn/low-margin items scoring high. WFS is computable
from a **single snapshot**, so `/price` and `/topmovers` ranking work day one (change
detectors stay suppressed until a baseline exists; `/topmovers` is empty during warmup).

**Phase 2 WFS (after depth semantics confirmed):**
`WFS = realizable_price_div × min(absorption_per_hr, assumed_supply_per_hr)^0.7` with
`realizable_price_div` upgraded to a depth-VWAP-to-intended-size and the `min()` term
encoding that worth-farming needs **both** a decent unit price **and** a sink that can
absorb your supply. `assumed_supply_per_hr` becomes a user-supplied per-strategy input.

**[P2]** WFS change is **decomposed** into a price term and a flow term: a WFS halving is
labeled DEMAND_COLLAPSE only when the **flow** term dominates, and PRICE_CRASH when the
**price** term dominates — no double-counting (requires a WFS baseline, hence Phase 2). In
both phases, without drop-rate data the absolute div/hr KPI is **omitted** (never relabel
a relative rank as an absolute KPI).

## 10. Build phasing

**Phase 1 — Collector + ship (this spec's implementation target):**
poe2scout client, SQLite ledger (deduped, log-space, Exalted-normalized), Discord bot,
slash commands, league autocomplete, scheduler, the Phase-1 simple detector, gating,
health channel + dead-man's switch, Docker/systemd. Runs 24/7 and **accumulates the
history** the heavy detectors require. Internal build stages (each independently
testable, suggested plan order):
1. poe2scout client + `normalize` + `store` (schema, dedup, windowed reads, prune).
2. Discord bot shell + slash commands + league autocomplete + `config`/settings.
3. Phase-1 `detector` + `gating` + `signals`/WFS + alert formatting + global top-K cap.
4. `scheduler` wiring + `health` (circuit breaker, pipeline-health channel, dead-man's
   switch) + Docker/systemd packaging.

**Phase 2 — Calibrate + full MLCS:** once weeks of data exist, measure field-window
semantics and refresh cadence empirically, build the replay/calibration harness, derive
`h/k/W`, and switch on CUSUM/Page-Hinkley/basket-detrending/FDR. Backfill from poe2scout
history endpoints to seed baselines on new items/leagues.

## 11. Error handling

Fetch retry with backoff → degrade gracefully (cross-check optional, core keeps running)
→ circuit breaker → loud health alert. Bad/missing rows skipped, never fatal. First poll
/ cold start = baseline only, no alerts (magnitude fast-path remains so genuine big moves
still surface during warmup). League rollover archives prior state and lazily rebuilds —
**never hard-wipes** on a single-poll change (that's an outage, not a rollover).

## 12. Testing (TDD)

- `signals` and `changepoint` are **pure functions** → exhaustive unit tests: outlier
  rejection, MAD=0, cold start, frozen-reference behavior, CUSUM on synthetic step/ramp/
  spike-revert series, demand-collapse on flat-price/flow-drop fixtures.
- `normalize` tested against saved JSON fixtures from both APIs (dual-encoding, currency
  pairs vs items).
- `store` tested against a temp DB (dedup, windowed reads, pruning, rollover).
- `gating`/`budget` tested for suppression correctness (stale, thin, early-league, FDR).
- Command handlers tested with discord mocks; autocomplete tested against a mock league list.

## 13. Residual risks (accepted, surfaced honestly)

1. **Field aggregation windows are undocumented** — the demand channel's correctness
   depends on getting first-differencing right; until measured, DEMAND_COLLAPSE latency
   could be 12h+ if `VolumeTraded` is itself 24h-smoothed.
2. **True refresh cadence unknown** — timing constants (`h`, persistence latency, warmup)
   are provisional until measured.
3. **poe2scout is a single load-bearing source** — a uniformly-wrong primary (e.g. a unit
   change) is only catchable via the level-divergence band against poe.ninja + schema pinning.
4. **Realized flow exists only for currency pairs** — for the large non-currency universe,
   demand is daily-resolution at best, so the flagship "silently dying farm" alert is
   coarse/laggy there. Inherent data limit, not an algorithm gap.
5. **Wash-trade manipulation** of volume can't be fully removed without counterparty
   identity the API doesn't expose; guards are probabilistic.
6. **Drop-rate tables** are absent from all APIs and stale every patch → STRATEGY_MARGIN
   is opt-in and structurally fragile.
7. **Calibration needs a replay corpus** collected first → Phase-1 false-alarm rates are
   estimates, not guarantees.
8. **Bias-toward-silence + 48h early-league mute** deliberately miss some genuine
   early/edge moves — accepted trade-off (a missed move costs one run; a false alert
   erodes trust in every future alert).

## 14. Open questions for implementation

- Confirm the exact poe2scout leagues endpoint shape (committed as the league-list source
  for Phase 1; poe.ninja is not used for leagues).
- Confirm whether poe2scout exposes a trade deep-link / trade id; if only poe.ninja
  `items[].tradeId` provides it, mark embed deep-links **best-effort** (unavailable when
  the optional poe.ninja path is down/404).
- Measure poe2scout `PriceHistory` granularity and the real recompute interval.
- Calibrate per-tier volume floors against observed PoE2 distributions per category.
- Decide drop-rate handling: hardcoded community tables vs user input vs omit (default omit).
- Confirm `Quantity`/`StockValue` semantics (fillable depth vs aggregate) before trusting
  bulk-depth VWAP.
