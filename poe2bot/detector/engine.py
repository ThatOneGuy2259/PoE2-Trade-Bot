from __future__ import annotations
from dataclasses import dataclass, field
from ..models import Observation, AlertEvent, Anchor, LiquidityTier
from ..signals import median, pct_from_log, wfs_phase1, relative_drop
from .gating import QualityConfig, hard_block_reason, has_min_samples, in_early_league

@dataclass(frozen=True)
class DetectConfig:
    floor_pct: float = 0.15
    cheap_floor_pct: float = 0.25
    cheap_price: float = 2.0
    fast_path_log: float = 0.40
    cooldown_s: int = 21600
    top_k: int = 8
    demand_drop: float = 0.50
    demand_min_trades_day: float = 10.0
    quality: QualityConfig = field(default_factory=QualityConfig)

@dataclass(frozen=True)
class PriceVerdict:
    event: AlertEvent | None
    new_mu_frozen: float | None
    reason: str | None
    fast_path: bool = False

def evaluate_price(obs: Observation, mu_frozen: float | None, baseline_logs: list[float],
                   last_fire_up_ts: int, last_fire_dn_ts: int, now_ts: int,
                   divine_exalt: float, cfg: DetectConfig,
                   early_league: bool = False) -> PriceVerdict:
    if mu_frozen is not None:
        reference = mu_frozen
    elif baseline_logs:
        reference = median(baseline_logs)
    else:
        reference = obs.log_price
    log_move = obs.log_price - reference
    move = pct_from_log(obs.log_price, reference)
    floor = cfg.cheap_floor_pct if obs.price_exalt < cfg.cheap_price else cfg.floor_pct
    fast = abs(log_move) >= cfg.fast_path_log
    fires = fast or abs(move) >= floor
    if not fires:
        return PriceVerdict(None, None, None)
    direction = "up" if move > 0 else "down"
    if early_league and direction == "down":
        return PriceVerdict(None, None, "early_league_mute")
    last_fire = last_fire_up_ts if direction == "up" else last_fire_dn_ts
    if last_fire and (now_ts - last_fire) < cfg.cooldown_s:
        return PriceVerdict(None, None, "cooldown")
    cls = "JUMP" if direction == "up" else "CRASH"
    wfs = wfs_phase1(obs.price_exalt, obs.liq_tier.gate, divine_exalt, obs.volume or 0.0)
    event = AlertEvent(item_id=obs.item_id, name=obs.name, cls=cls, direction=direction,
                       magnitude=log_move, pct_move=move, baseline=reference,
                       current=obs.log_price, severity=abs(log_move), liq_tier=obs.liq_tier,
                       trade_id=obs.trade_id, wfs=wfs)
    return PriceVerdict(event, obs.log_price, None, fast_path=fast)


def evaluate_demand(obs: Observation, volume_baseline: list[float],
                    early_league: bool, cfg: DetectConfig) -> AlertEvent | None:
    if early_league or obs.volume is None or not volume_baseline:
        return None
    base = median(volume_baseline)
    if base < cfg.demand_min_trades_day:
        return None
    drop = relative_drop(obs.volume, base)
    if drop < cfg.demand_drop:
        return None
    return AlertEvent(item_id=obs.item_id, name=obs.name, cls="DEMAND_COLLAPSE",
                      direction="down", magnitude=-drop, pct_move=-drop, baseline=base,
                      current=obs.volume, severity=drop, liq_tier=obs.liq_tier,
                      trade_id=obs.trade_id, wfs=0.0)


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
                 league_started_at: int, now_ts: int, cfg: DetectConfig):
    early = in_early_league(now_ts, league_started_at, cfg.quality)
    candidates: list[AlertEvent] = []
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
                                 st["last_fire_dn_ts"], now_ts, anchor.divine_exalt, cfg,
                                 early_league=early)
        if verdict.event is not None:
            ev = verdict.event
            if verdict.fast_path:
                # fast-path: fire immediately, bypassing 2-of-3 and the min_samples gate
                await _reset_pending(store, obs.item_id, ev.direction)
                await _fire_state(store, obs.item_id, ev.direction, verdict.new_mu_frozen, now_ts)
                candidates.append(ev)
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
                    candidates.append(ev)
        elif verdict.reason in ("cooldown", "early_league_mute"):
            await store.record_alert(_suppressed(obs), fired=False,
                                     suppressed_reason=verdict.reason, src_ts=obs.src_ts)
        else:
            await _reset_pending(store, obs.item_id, "up")
            await _reset_pending(store, obs.item_id, "down")
        vol_base = await store.volume_window(obs.item_id, obs.src_ts - 48 * 3600)
        dem = evaluate_demand(obs, vol_base, early, cfg)
        if dem is not None:
            candidates.append(dem)
    candidates.sort(key=lambda e: e.severity, reverse=True)
    kept = candidates[: cfg.top_k]
    overflow_events = candidates[cfg.top_k:]
    for ev in kept:
        await store.record_alert(ev, fired=True, suppressed_reason=None, src_ts=now_ts)
    for ev in overflow_events:
        await store.record_alert(ev, fired=False, suppressed_reason="overflow_capped", src_ts=now_ts)
    return kept, len(overflow_events)
