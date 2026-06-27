from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum


class LiquidityTier(IntEnum):
    LOW = 0
    MED = 1
    HIGH = 2

    @property
    def gate(self) -> float:
        # LOW is discounted but non-zero: rare-but-valuable items (e.g. Mirror) still get a
        # meaningful Worth-Farming Score instead of collapsing to 0.
        return {LiquidityTier.LOW: 0.3, LiquidityTier.MED: 0.6, LiquidityTier.HIGH: 1.0}[self]


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
    price_exalt: float = 0.0       # current price expressed in the three primary currencies
    price_div: float = 0.0
    price_chaos: float = 0.0
    low_confidence: bool = False   # True for LOW-liquidity items (price may be unreliable)


@dataclass(frozen=True)
class Anchor:
    divine_exalt: float
    chaos_divine: float
