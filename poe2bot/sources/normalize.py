from __future__ import annotations
from ..models import Observation, LiquidityTier, Anchor
from ..signals import to_log_price


def tier_from_volume(volume: float | None, low: float = 100.0, med: float = 1000.0) -> LiquidityTier:
    if volume is None or volume < low:
        return LiquidityTier.LOW
    if volume < med:
        return LiquidityTier.MED
    return LiquidityTier.HIGH


def normalize_currency(raw: dict, league_id: str, anchor: Anchor, src_ts: int) -> list[Observation]:
    out: list[Observation] = []
    for it in raw.get("items", []):
        price = it.get("currentPrice")
        if price is None or price <= 0:
            continue
        qty = it.get("currentQuantity")
        volume = float(qty) if qty is not None else None
        out.append(Observation(
            item_id=str(it["apiId"]), league_id=league_id, src_ts=src_ts, wall_ts=src_ts,
            name=it.get("name", it["apiId"]), category="currency", is_currency_pair=True,
            log_price=to_log_price(float(price)), price_exalt=float(price),
            volume=volume, vol_daily=None, stock=None, doi=None,
            liq_tier=tier_from_volume(volume), trade_id=it.get("tradeId"), valid=True))
    return out
