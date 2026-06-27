from __future__ import annotations
from dataclasses import dataclass, field
from ..models import Observation, AlertEvent, LiquidityTier
from ..signals import median, pct_from_log, wfs_phase1
from .gating import QualityConfig

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
