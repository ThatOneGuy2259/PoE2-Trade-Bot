# Self-normalizing CUSUM change-point detector — design (for review)

Upgrades the statistical confirmation path from ad-hoc 2-of-3 persistence to a two-sided,
self-normalizing CUSUM. Behind a toggle (`use_cusum`, default ON) with 2-of-3 retained for
instant rollback. The fast-path, per-category floors, LOW-liq/cheap guards, junk-price gate,
early-league mute, cooldowns, demand-collapse, and per-category top-K are ALL preserved.

## Why self-normalizing (no calibration corpus needed)
Standardize each poll's log-price deviation from the reference by the baseline window's robust
scale (MAD → sigma). Then the CUSUM slack `k` and threshold `h` are DIMENSIONLESS (sigma units)
and use textbook defaults that don't need per-item tuning. A replay/calibration harness to tune
h/k on collected history is the NEXT increment (deferred), not a blocker.

## Algorithm (per item, per poll; only when cfg.use_cusum)
**FROZEN reference (review fix — tabular CUSUM needs a fixed in-control target μ0).** CUSUM must
accumulate against a stable reference, NOT the per-poll-recomputed `median(baseline_logs)` (a
moving target mixes incomparable z's and lets a slow ramp be absorbed by the median). So:
- If `mu_frozen` is set → `reference = mu_frozen`, accumulate CUSUM.
- Elif warmup just completed (`has_min_samples(len(baseline_logs))`) → FREEZE this poll:
  `reference = median(baseline_logs)`, return it as `new_mu_frozen` to be persisted; start
  accumulating from the carried-in (0,0).
- Else (still warming up) → do NOT accumulate (carry CUSUM 0,0), do NOT fire; `reference` is
  irrelevant. (Mirrors today's insufficient_samples warmup; CUSUM only runs against a frozen μ0.)

`baseline_logs` = the existing 24h `price_log_window`. Then (only when a frozen reference exists):
- `scale = max(1.4826 * mad(baseline_logs), cfg.cusum_min_scale)`; if `baseline_logs` is empty,
  `scale = cfg.cusum_min_scale` (GUARD: never call `mad([])`, which raises). Robust sigma in log
  units; the floor stops a near-constant series from making scale→0 and the CUSUM hypersensitive.
- `z = (obs.log_price - reference) / scale`   (standardized deviation, in log space)
- `s_pos = max(0, prev_pos + z - k)` ; `s_neg = max(0, prev_neg - z - k)`   (two-sided)
- A CUSUM "hit": `(s_pos >= h and move>0)` (UP) or `(s_neg >= h and move<0)` (DOWN). Sign must
  agree with the current move direction (an item reverting toward the reference shouldn't fire).
- The CUSUM is UPDATED every poll (each poll is a fresh market snapshot — see Note), but a FIRE
  additionally requires the existing economic + safety gates: `|move%| >= floor`, not cooldown,
  not early-league-down, not junk-gated.
- On FIRE: reset `s_pos = s_neg = 0`, re-freeze `mu_frozen = current log_price`, set cooldown ts.
- The fast-path (`|log_move| >= fast_path_log and not is_low`) still fires IMMEDIATELY, bypassing
  the CUSUM AND the warmup/min_samples gate (subject to the same cooldown/early-league/junk gates
  as today). It resets the CUSUM on fire.

Note (per-poll accumulation is intentional): src_ts = fetch time (always new), so every poll is a
fresh, independent market snapshot of poe2scout's CurrentPrice — not a re-stored stale value.
Accumulating against the FROZEN reference correctly fires on a sustained shift (~2 polls for a
clear move) and decays (s_pos -= k each poll) when the price reverts. No content-dedup needed.

### Defaults (DetectConfig)
- `use_cusum: bool = True`
- `cusum_k: float = 0.5`   (slack; textbook)
- `cusum_h: float = 5.0`   (decision threshold; conservative → favors precision over recall)
- `cusum_min_scale: float = 0.05`  (sigma floor ≈ 5% in log units)
Sanity: a stable item (scale floored at 5%) seeing a +15% move → z=3, accumulates (3−0.5)=2.5/poll
→ crosses h=5 in ~2 polls (≈ today's 2-of-3). A volatile item (scale 10%) needs the move sustained
longer to fire (noise filtering) — the intended upgrade. Sub-floor drifts never alert (floor gate).

## Integration points
### models/PriceVerdict (engine.py)
Add fields: `cusum_pos: float = 0.0`, `cusum_neg: float = 0.0`, `confirmed: bool = False`.
`confirmed=True` means "fire now" (set by the CUSUM-fire and fast-path branches); lets `detect`
fire directly without the 2-of-3 wrapper. Every returned verdict carries the post-update CUSUM
state so `detect` can persist it.

### evaluate_price (engine.py)
New params `cusum_pos: float = 0.0, cusum_neg: float = 0.0` (current state). Branch on
`cfg.use_cusum`:
- `use_cusum=False`: the EXISTING 2-of-3 body, unchanged, carrying the input cusum_pos/neg through
  unchanged in the returned verdict (no-op).
- `use_cusum=True` — EXACT ORDER (review fixes folded in):
  1. Determine the frozen reference (per Algorithm: mu_frozen, else freeze-on-warmup, else warming).
  2. `fast = abs(log_move) >= cfg.fast_path_log and not is_low`.
  3. If still warming up (no frozen reference and not fast): return event=None, reason=None,
     carry cusum (0,0). [fast-path during warmup is handled in step 6, NOT gated here]
  4. Compute scale/z; `s_pos/s_neg` from carried-in state; `cusum_hit` with sign-agreement.
  5. `candidate = fast or (cusum_hit and abs(move) >= floor)`. If NOT candidate → return event=None
     carrying accumulated s_pos/s_neg (still accumulating) + new_mu_frozen if just frozen.
  6. A fire candidate exists. Apply suppressors in order, CARRYING the accumulated cusum so it
     keeps building across the suppression:
     - junk gate: `reference <= log(min_alert_price_exalt)` → return "below_min_price" (carry cusum).
       [AFTER the candidate check → only would-be alerts record it, not every floor-sitter every poll]
     - `early_league and direction=="down"` → return "early_league_mute" (carry cusum).
     - cooldown → return "cooldown" (carry cusum).
     - `not fast and not has_min_samples(...)` → return "insufficient_samples" (carry cusum).
       [min_samples gates ONLY the CUSUM path; fast-path fires regardless of sample count]
  7. FIRE: build event; return with `confirmed=True`, `fast_path=fast`, cusum reset (0,0),
     `new_mu_frozen = obs.log_price`.

### detect (engine.py)
- Read `cusum_pos`/`cusum_neg` from detector_state, pass into evaluate_price.
- When `use_cusum`: ALWAYS persist `verdict.cusum_pos`/`verdict.cusum_neg` (and `new_mu_frozen` when
  not None — the freeze-on-warmup case) to detector_state every poll, in ONE
  `update_detector_state` call, so accumulation + the frozen reference survive between polls.
- Verdict handling unchanged in shape: `if verdict.event is not None: if verdict.fast_path or
  verdict.confirmed: fire; elif not has_min_samples(...): insufficient_samples; else: 2-of-3 pend`.
  In CUSUM mode returned events ALWAYS set confirmed/fast, so the 2-of-3 branch and the inline
  has_min_samples elif are only reached in 2-of-3 mode — `use_cusum=False` is byte-identical to today.
- On FIRE in CUSUM mode: persist mu_frozen + last_fire_ts + cusum reset (0,0) in ONE
  `update_detector_state` (merge the prior two calls — review Minor).
- Add `"insufficient_samples"` to detect's recorded-reason tuple (CUSUM warmup returns it via
  evaluate_price's reason, vs today where detect records it inline for the 2-of-3 path).

### store (state schema + migration)
- detector_state gains `cusum_pos REAL DEFAULT 0`, `cusum_neg REAL DEFAULT 0`.
- `_STATE_DEFAULTS` gains `cusum_pos: 0.0, cusum_neg: 0.0` (so get/update whitelist them).
- MIGRATION for existing DBs (CREATE TABLE IF NOT EXISTS won't add columns): on `Store.open`, after
  executescript, read `PRAGMA table_info(detector_state)` and `ALTER TABLE detector_state ADD
  COLUMN cusum_pos REAL DEFAULT 0` / `cusum_neg` for any missing column. Idempotent.

## Toggle / rollback
`cfg.use_cusum=False` reverts to the exact current 2-of-3 behavior (the existing code path,
unchanged). Flipping it is a config change (DetectConfig in main.py / could be env-driven later).
Default ON ships CUSUM live; the conservative h=5 favors fewer false alarms.

## What is NOT changed
demand-collapse, hard_block gating, anchor/currency conversion, per-category top-K, the junk gate,
fast-path semantics, cooldowns, alert formatting. CUSUM only replaces the 2-of-3 *confirmation*.

## Calibration caveat (honest)
h/k/min_scale are principled defaults, NOT tuned to PoE2 data (only ~1 day collected). The
self-normalization makes them reasonable out-of-the-box. The replay harness (next increment) will
replay collected obs through varied (h, k, min_scale) to measure alert volume / would-be precision
and tune them. Until then, the toggle is the safety valve.

## Testing intent (TDD) — review fixes folded in
- **Legacy isolation (REQUIRED):** with `use_cusum` defaulting True, the existing single-call
  evaluate_price tests would run under CUSUM and break. So `test_engine.py`'s `_cfg()` helper sets
  `use_cusum=False`, AND the bare-`DetectConfig()` junk-gate tests pass `use_cusum=False`, so all
  legacy assertions keep exact 2-of-3 semantics. The CUSUM tests below use the default (True).
- **Controlled-MAD test (REQUIRED — every existing fixture is a constant series, MAD=0):** build a
  `baseline_logs` with a KNOWN non-floored MAD (e.g. so `1.4826*mad ≈ 0.10`) and assert a +15% move
  accumulates over MORE polls before firing than the floored-scale (constant-series) case — proving
  the volatility-adaptive persistence that is the whole point.
- **Cold-start (REQUIRED):** `evaluate_price` with `baseline_logs=[]` must NOT raise (guard
  `mad([])`), `scale=min_scale`, and (via warmup) does not fire.
- CUSUM accumulation: a frozen-reference sustained +move crosses h after the expected polls and
  fires; a transient spike that reverts does NOT fire (s_pos decays by k); sign-agreement (s_pos
  high but current move negative → no up-fire).
- floor gate: a CUSUM hit with |move|<floor does NOT fire; fires once move crosses floor.
- fast-path: immediate-fires under CUSUM EVEN during warmup (bypasses min_samples); junk gate still
  suppresses (below_min_price) only on would-be-fire polls; cooldown/early-league suppress while
  CUSUM keeps accumulating.
- reference freeze: mu_frozen is set at warmup completion (before any fire) and persisted; CUSUM
  accumulates against it.
- state: detector_state cusum_pos/neg round-trip; reset to 0 on fire; ONE update per poll; the
  PRAGMA-based migration adds the columns to a pre-existing detector_state table.
- toggle: `use_cusum=False` reproduces the existing 2-of-3 detect()-level tests exactly.
- per-category cap / demand / multi-category unaffected.
