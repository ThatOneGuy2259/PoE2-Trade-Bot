# Phase 2 follow-ups

Deferred items from the Phase-1 final whole-branch review (2026-06-27). None block
Phase-1 (ship the pipeline + collect a clean ledger with provisional thresholds);
all are improvements to make once real data exists.

## From the final review (Minor / deferred)
- **alert_log timebase is mixed:** fired rows are written with wall-clock `now_ts`
  (engine.py) while suppressed rows use server `obs.src_ts`. Pick one scale before
  running time-based queries over `alert_log`.
- **Top-K severity mixes units:** price `severity = abs(log_move)` vs demand
  `severity = drop` (0–1) are sorted in one list, so a large demand drop can outrank a
  larger price move (and vice versa). Normalize to a common scale before ranking.
- **Volume-field semantics now partly resolved:** the bot tiers liquidity and judges
  demand on the daily `PriceLogs[].Quantity` series (real activity), and keeps the live
  `CurrentQuantity` snapshot as `stock` for future supply-vs-flow analysis. The *exact*
  meaning of `PriceLogs.Quantity` is still not formally documented (~900/day for Mirror
  seems high for literal trades — it may be a listings/observation count), so the LOW/MED/
  HIGH thresholds (5k/100k) are calibrated empirically against the live distribution, not
  from spec. Confirm the field's true unit before trusting the absolute thresholds, and
  add proper count-noise (Poisson/NegBin) gating for thin markets instead of the flat
  `demand_min_volume` floor.
- **`evaluate_demand` has no `min_samples` gate** (only the ≥10 trades/day floor), so it
  can fire off a 1–2 sample baseline early in warmup. Add a sample-count gate.
- **No per-snapshot timestamp from poe2scout** (confirmed live: the Currencies response
  has no `epoch`; only daily `PriceLogs[].Time`). Phase 1 therefore uses the bot's own
  fetch time as `src_ts`, so every poll stores a row per item even when the price is
  unchanged (oversampling). Harmless for the frozen-reference Phase-1 detector, but
  Phase-2's MAD/CUSUM will need content-based dedup (skip storing unchanged values) —
  and that dedup must NOT break 2-of-3 persistence, which re-evaluates the same elevated
  price across polls.
- **`prune` only trims `obs`.** `alert_log`, `demfire:`/`pend:` setting keys, and the
  `basket`/`daily_rollup` tables (when added) grow unbounded — extend pruning.
- **Early-league `started_at` is a proxy:** bootstrapped from first-poll time, not the
  real league start (poe2scout exposes none). A bot first deployed mid-league will treat
  its first 48h as "early league." Acceptable for Phase 1; revisit if a real start date
  becomes available.
- **Graceful shutdown:** `main.py` does not call `scheduler.shutdown()` in `finally`; a
  set-but-uncached health channel silently drops the message (add a `fetch_channel`
  fallback). Low risk; tidy in Phase 2.
- **`update_detector_state` validates field names mid-loop** — validate-all-first is the
  clean tidy (unreachable today since all callers pass whitelisted fields).
- Test hygiene: a few guard branches untested (`ping_dead_man` failure, notifier
  `channel is None`); tests rely on pytest `asyncio_mode=auto` rather than per-test
  markers.

## The Phase-2 algorithm itself (per spec §6/§10)
CUSUM / Page-Hinkley change-point detection in log-space against a frozen reference,
basket detrending + currency-regime suppressor, Benjamini-Hochberg FDR fleet budget,
first-differenced realized flow (once the field's aggregation window is measured),
diurnal/hour-of-week deseasonalization, and the optional poe.ninja cross-check. All
require a replay corpus of collected poe2scout history to calibrate `h/k/W` — which is
exactly what the Phase-1 collector is now accumulating.
