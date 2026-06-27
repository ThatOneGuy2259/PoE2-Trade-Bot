from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum


class LiquidityTier(IntEnum):
    LOW = 0
    MED = 1
    HIGH = 2

    @property
    def gate(self) -> float:
        return {LiquidityTier.LOW: 0.0, LiquidityTier.MED: 0.6, LiquidityTier.HIGH: 1.0}[self]


@dataclass(frozen=True)
class Observation:
    item_id: str
    league_id: str
    src_ts: int
    wall_ts: int
    name: str
    category: str
    is_currency_pair: bool
    log_price: float
    price_exalt: float
    volume: float | None
    vol_daily: float | None
    stock: float | None
    doi: float | None
    liq_tier: LiquidityTier
    trade_id: str | None
    valid: bool
    gap: bool = False


@dataclass(frozen=True)
class AlertEvent:
    item_id: str
    name: str
    cls: str          # "JUMP" | "CRASH" | "DEMAND_COLLAPSE"
    direction: str    # "up" | "down"
    magnitude: float  # log move vs frozen reference
    pct_move: float
    baseline: float
    current: float
    severity: float
    liq_tier: LiquidityTier
    trade_id: str | None
    wfs: float


@dataclass(frozen=True)
class Anchor:
    divine_exalt: float
    chaos_divine: float
