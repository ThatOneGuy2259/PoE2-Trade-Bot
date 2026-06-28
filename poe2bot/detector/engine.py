from __future__ import annotations
import math
from dataclasses import dataclass, field
from ..models import Observation, AlertEvent, Anchor, LiquidityTier
from ..signals import median, mad, pct_from_log, wfs_phase1, relative_drop, to_currencies
from .gating import QualityConfig, hard_block_reason, has_min_samples, in_early_league

@dataclass(frozen=True)
class DetectConfig:
    floor_pct: float = 0.15
    cheap_floor_pct: float = 0.25
    cheap_price: float = 2.0
    fast_path_log: float = 0.40
    low_liq_floor: float = 0.40      # LOW-liquidity items need a much bigger move to fire
    cooldown_s: int = 21600
    top_k: int = 8
    demand_drop: float = 0.50
    demand_min_volume: float = 5000.0  # daily-volume floor below which demand is too noisy
    min_alert_price_exalt: float = 1.0  # items whose baseline sits at/below this (the 1-ex display
                                        # floor) carry no real price signal — gate their alerts. 0 disables.
    # Self-normalizing CUSUM (default ON): the statistical confirmation path. Set use_cusum=False to
    # roll back to the 2-of-3 persistence path. k/h/min_scale are dimensionless (sigma units) so they
    # need no per-item calibration; tune later via the replay harness.
    use_cusum: bool = True
    cusum_k: float = 0.5            # slack/reference allowance
    cusum_h: float = 5.0           # decision threshold (higher = fewer false alarms)
    cusum_min_scale: float = 0.05  # robust-sigma floor in log units (~5%) — stops a near-constant
                                   # series from making the CUSUM hypersensitive
    quality: QualityConfig = field(default_factory=QualityConfig)

@dataclass(frozen=True)
class PriceVerdict:
    event: AlertEvent | None
    new_mu_frozen: float | None
    reason: str | None
    fast_path: bool = False
    confirmed: bool = False        # CUSUM-confirmed fire (detect fires directly, no 2-of-3)
    cusum_pos: float = 0.0         # post-update CUSUM state to persist
    cusum_neg: float = 0.0

def _floor_for(obs: Observation, cfg: DetectConfig, category_floors, is_low: bool) -> float:
    # Per-category threshold sets the BASE spike floor; the LOW-liquidity and cheap-item guards are
    # applied on top via max(), so a category floor can tighten/set the baseline but never weaken them.
    base = (category_floors or {}).get(obs.category, cfg.floor_pct)
    if is_low:
        return max(base, cfg.low_liq_floor)
    if obs.price_exalt < cfg.cheap_price:
        return max(base, cfg.cheap_floor_pct)
    return base


def _is_junk(reference: float, cfg: DetectConfig) -> bool:
    # An item whose BASELINE sits at/below the 1-exalt display floor has no real price signal.
    return cfg.min_alert_price_exalt > 0 and reference <= math.log(cfg.min_alert_price_exalt)


def _build_event(obs: Observation, direction: str, log_move: float, move: float,
                 reference: float, anchor: Anchor, is_low: bool) -> AlertEvent:
    cls = "JUMP" if direction == "up" else "CRASH"
    wfs = wfs_phase1(obs.price_exalt, obs.liq_tier.gate, anchor.divine_exalt, obs.volume or 0.0)
    px, pdiv, pchaos = to_currencies(obs.price_exalt, anchor.divine_exalt, anchor.chaos_divine)
    return AlertEvent(item_id=obs.item_id, name=obs.name, cls=cls, direction=direction,
                      magnitude=log_move, pct_move=move, baseline=reference, current=obs.log_price,
                      severity=abs(log_move), liq_tier=obs.liq_tier, trade_id=obs.trade_id, wfs=wfs,
                      price_exalt=px, price_div=pdiv, price_chaos=pchaos, low_confidence=is_low)


def evaluate_price(obs: Observation, mu_frozen: float | None, baseline_logs: list[float],
                   last_fire_up_ts: int, last_fire_dn_ts: int, now_ts: int,
                   anchor: Anchor, cfg: DetectConfig,
                   early_league: bool = False,
                   category_floors: dict[str, float] | None = None,
                   cusum_pos: float = 0.0, cusum_neg: float = 0.0) -> PriceVerdict:
    is_low = obs.liq_tier == LiquidityTier.LOW
    floor = _floor_for(obs, cfg, category_floors, is_low)
    if cfg.use_cusum:
        return _evaluate_cusum(obs, mu_frozen, baseline_logs, last_fire_up_ts, last_fire_dn_ts,
                               now_ts, anchor, cfg, early_league, floor, is_low, cusum_pos, cusum_neg)
    # ---- legacy 2-of-3 path (behavior byte-identical to before CUSUM) ----
    if mu_frozen is not None:
        reference = mu_frozen
    elif baseline_logs:
        reference = median(baseline_logs)
    else:
        reference = obs.log_price
    log_move = obs.log_price - reference
    move = pct_from_log(obs.log_price, reference)
    fast = abs(log_move) >= cfg.fast_path_log and not is_low
    if not (fast or abs(move) >= floor):
        return PriceVerdict(None, None, None, cusum_pos=cusum_pos, cusum_neg=cusum_neg)
    if _is_junk(reference, cfg):
        return PriceVerdict(None, None, "below_min_price", cusum_pos=cusum_pos, cusum_neg=cusum_neg)
    direction = "up" if move > 0 else "down"
    if early_league and direction == "down":
        return PriceVerdict(None, None, "early_league_mute", cusum_pos=cusum_pos, cusum_neg=cusum_neg)
    last_fire = last_fire_up_ts if direction == "up" else last_fire_dn_ts
    if last_fire and (now_ts - last_fire) < cfg.cooldown_s:
        return PriceVerdict(None, None, "cooldown", cusum_pos=cusum_pos, cusum_neg=cusum_neg)
    event = _build_event(obs, direction, log_move, move, reference, anchor, is_low)
    return PriceVerdict(event, obs.log_price, None, fast_path=fast,
                        cusum_pos=cusum_pos, cusum_neg=cusum_neg)


def _evaluate_cusum(obs: Observation, mu_frozen: float | None, baseline_logs: list[float],
                    last_fire_up_ts: int, last_fire_dn_ts: int, now_ts: int, anchor: Anchor,
                    cfg: DetectConfig, early_league: bool, floor: float, is_low: bool,
                    cusum_pos: float, cusum_neg: float) -> PriceVerdict:
    # Reference must be FROZEN for tabular CUSUM (a moving median mixes incomparable deviations).
    # mu_frozen if set; else freeze it the poll warmup completes; else keep warming (don't accumulate).
    warming = mu_frozen is None and not has_min_samples(len(baseline_logs), cfg.quality)
    new_mu_frozen = None
    if mu_frozen is not None:
        reference = mu_frozen
    elif not warming:
        reference = median(baseline_logs)
        new_mu_frozen = reference                         # freeze now; detect persists it
    else:
        reference = median(baseline_logs) if baseline_logs else obs.log_price
    log_move = obs.log_price - reference
    move = pct_from_log(obs.log_price, reference)
    fast = abs(log_move) >= cfg.fast_path_log and not is_low
    if warming:
        s_pos, s_neg, cusum_hit = cusum_pos, cusum_neg, False   # no stable reference yet
    else:
        scale = (max(1.4826 * mad(baseline_logs), cfg.cusum_min_scale)
                 if baseline_logs else cfg.cusum_min_scale)     # robust sigma; guard mad([])
        z = (obs.log_price - reference) / scale
        s_pos = max(0.0, cusum_pos + z - cfg.cusum_k)
        s_neg = max(0.0, cusum_neg - z - cfg.cusum_k)
        cusum_hit = (s_pos >= cfg.cusum_h and move > 0) or (s_neg >= cfg.cusum_h and move < 0)
    # Fire candidate = an immediate big move (fast) OR a CUSUM hit that also clears the economic floor.
    if not (fast or (cusum_hit and abs(move) >= floor)):
        return PriceVerdict(None, new_mu_frozen, None, cusum_pos=s_pos, cusum_neg=s_neg)
    # Candidate exists -> apply suppressors, CARRYING the accumulated CUSUM so it keeps building.
    if _is_junk(reference, cfg):
        return PriceVerdict(None, new_mu_frozen, "below_min_price", cusum_pos=s_pos, cusum_neg=s_neg)
    direction = "up" if move > 0 else "down"
    if early_league and direction == "down":
        return PriceVerdict(None, new_mu_frozen, "early_league_mute", cusum_pos=s_pos, cusum_neg=s_neg)
    last_fire = last_fire_up_ts if direction == "up" else last_fire_dn_ts
    if last_fire and (now_ts - last_fire) < cfg.cooldown_s:
        return PriceVerdict(None, new_mu_frozen, "cooldown", cusum_pos=s_pos, cusum_neg=s_neg)
    if not fast and not has_min_samples(len(baseline_logs), cfg.quality):
        return PriceVerdict(None, new_mu_frozen, "insufficient_samples", cusum_pos=s_pos, cusum_neg=s_neg)
    event = _build_event(obs, direction, log_move, move, reference, anchor, is_low)
    return PriceVerdict(event, obs.log_price, None, fast_path=fast, confirmed=True,
                        cusum_pos=0.0, cusum_neg=0.0)              # reset on fire


def evaluate_demand(obs: Observation, volume_baseline: list[float],
                    early_league: bool, cfg: DetectConfig,
                    anchor: Anchor | None = None) -> AlertEvent | None:
    if early_league or obs.volume is None or not volume_baseline:
        return None
    if cfg.min_alert_price_exalt > 0 and obs.price_exalt <= cfg.min_alert_price_exalt:
        return None                            # worthless floor-priced item: demand drop isn't news
    base = median(volume_baseline)
    if base < cfg.demand_min_volume:           # too thin for the demand signal to be trustworthy
        return None
    drop = relative_drop(obs.volume, base)
    if drop < cfg.demand_drop:
        return None
    a = anchor or Anchor(1.0, 1.0)
    px, pdiv, pchaos = to_currencies(obs.price_exalt, a.divine_exalt, a.chaos_divine)
    return AlertEvent(item_id=obs.item_id, name=obs.name, cls="DEMAND_COLLAPSE",
                      direction="down", magnitude=-drop, pct_move=-drop, baseline=base,
                      current=obs.volume, severity=drop, liq_tier=obs.liq_tier,
                      trade_id=obs.trade_id, wfs=0.0, price_exalt=px, price_div=pdiv,
                      price_chaos=pchaos, low_confidence=(obs.liq_tier == LiquidityTier.LOW))


def _suppressed(obs: Observation) -> AlertEvent:
    return AlertEvent(item_id=obs.item_id, name=obs.name, cls="SUPPRESSED", direction="none",
                      magnitude=0.0, pct_move=0.0, baseline=0.0, current=obs.log_price,
                      severity=0.0, liq_tier=obs.liq_tier, trade_id=obs.trade_id, wfs=0.0)


async def _bump_pending(store, item_id: str, direction: str) -> int:
    key = f"pend:{item_id}:{direction}"
    cur = int(await store.get_setting(key) or "0") + 1
    await store.set_setting(key, str(cur))
    return cur


async def _reset_pending(store, item_id: str, direction: str) -> None:
    await store.set_setting(f"pend:{item_id}:{direction}", "0")


async def _fire_state(store, item_id: str, direction: str, mu_frozen: float, now_ts: int) -> None:
    fire_field = "last_fire_up_ts" if direction == "up" else "last_fire_dn_ts"
    await store.update_detector_state(item_id, mu_frozen=mu_frozen, **{fire_field: now_ts})


async def detect(store, observations: list[Observation], anchor: Anchor,
                 league_started_at: int, now_ts: int, cfg: DetectConfig,
                 category_floors: dict[str, float] | None = None):
    early = in_early_league(now_ts, league_started_at, cfg.quality)
    # Candidates are grouped by category so the top-K cap applies PER category — a volatile
    # category can't crowd out alerts for the others the user enabled.
    candidates_by_cat: dict[str, list[AlertEvent]] = {}
    def _add(category: str, ev: AlertEvent) -> None:
        candidates_by_cat.setdefault(category, []).append(ev)
    for obs in observations:
        last = await store.last_observation(obs.item_id)
        prev_src_ts = last.src_ts if last else None
        await store.insert_observation(obs)
        # window in SERVER-TIMESTAMP space (not wall-clock) so the baseline is found
        # regardless of the offset between API epoch and wall clock.
        baseline_logs = await store.price_log_window(obs.item_id, obs.src_ts - 24 * 3600)
        reason = hard_block_reason(obs, prev_src_ts, now_ts, cfg.quality)
        if reason:
            await store.record_alert(_suppressed(obs), fired=False,
                                     suppressed_reason=reason, src_ts=obs.src_ts)
            continue
        st = await store.get_detector_state(obs.item_id)
        verdict = evaluate_price(obs, st["mu_frozen"], baseline_logs, st["last_fire_up_ts"],
                                 st["last_fire_dn_ts"], now_ts, anchor, cfg,
                                 early_league=early, category_floors=category_floors,
                                 cusum_pos=st["cusum_pos"], cusum_neg=st["cusum_neg"])
        if cfg.use_cusum:
            # CUSUM confirms inside evaluate_price (no 2-of-3). Persist the accumulated CUSUM (and
            # the frozen reference) EVERY poll in one update; on fire, reset + stamp last_fire.
            if verdict.event is not None:
                ev = verdict.event
                fire_field = "last_fire_up_ts" if ev.direction == "up" else "last_fire_dn_ts"
                await store.update_detector_state(obs.item_id, mu_frozen=verdict.new_mu_frozen,
                                                  cusum_pos=0.0, cusum_neg=0.0, **{fire_field: now_ts})
                _add(obs.category, ev)
            else:
                fields = {"cusum_pos": verdict.cusum_pos, "cusum_neg": verdict.cusum_neg}
                if verdict.new_mu_frozen is not None:
                    fields["mu_frozen"] = verdict.new_mu_frozen
                await store.update_detector_state(obs.item_id, **fields)
                if verdict.reason in ("cooldown", "early_league_mute", "below_min_price",
                                      "insufficient_samples"):
                    await store.record_alert(_suppressed(obs), fired=False,
                                             suppressed_reason=verdict.reason, src_ts=obs.src_ts)
        elif verdict.event is not None:
            ev = verdict.event
            if verdict.fast_path:
                # fast-path: fire immediately, bypassing 2-of-3 and the min_samples gate
                await _reset_pending(store, obs.item_id, ev.direction)
                await _fire_state(store, obs.item_id, ev.direction, verdict.new_mu_frozen, now_ts)
                _add(obs.category, ev)
            elif not has_min_samples(len(baseline_logs), cfg.quality):
                await store.record_alert(_suppressed(obs), fired=False,
                                         suppressed_reason="insufficient_samples", src_ts=obs.src_ts)
                await _reset_pending(store, obs.item_id, "up")
                await _reset_pending(store, obs.item_id, "down")
            else:
                n = await _bump_pending(store, obs.item_id, ev.direction)
                if n >= 2:
                    await _reset_pending(store, obs.item_id, ev.direction)
                    await _fire_state(store, obs.item_id, ev.direction, verdict.new_mu_frozen, now_ts)
                    _add(obs.category, ev)
        elif verdict.reason in ("cooldown", "early_league_mute", "below_min_price"):
            await store.record_alert(_suppressed(obs), fired=False,
                                     suppressed_reason=verdict.reason, src_ts=obs.src_ts)
        else:
            await _reset_pending(store, obs.item_id, "up")
            await _reset_pending(store, obs.item_id, "down")
        vol_base = await store.volume_window(obs.item_id, obs.src_ts - 48 * 3600)
        dem = evaluate_demand(obs, vol_base, early, cfg, anchor)
        if dem is not None:
            last_dem = int(await store.get_setting(f"demfire:{obs.item_id}") or "0")
            if not last_dem or (now_ts - last_dem) >= cfg.cooldown_s:
                await store.set_setting(f"demfire:{obs.item_id}", str(now_ts))
                _add(obs.category, dem)
            else:
                await store.record_alert(dem, fired=False, suppressed_reason="demand_cooldown", src_ts=now_ts)
    # Per-category top-K cap, then a final severity sort across the kept set for display order.
    kept: list[AlertEvent] = []
    overflow = 0
    for evs in candidates_by_cat.values():
        evs.sort(key=lambda e: e.severity, reverse=True)
        kept.extend(evs[: cfg.top_k])
        for ev in evs[cfg.top_k:]:
            await store.record_alert(ev, fired=False, suppressed_reason="overflow_capped", src_ts=now_ts)
        overflow += max(0, len(evs) - cfg.top_k)
    kept.sort(key=lambda e: e.severity, reverse=True)
    for ev in kept:
        await store.record_alert(ev, fired=True, suppressed_reason=None, src_ts=now_ts)
    return kept, overflow
