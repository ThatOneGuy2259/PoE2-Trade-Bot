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
    """Normalize a poe2scout Currencies/ByCategory response into Observations.

    Live API fields are PascalCase: Items[].ApiId / Text / CurrentPrice / CurrentQuantity.
    There is no trade id on currency items (trade_id stays None — deep links are best-effort).
    CurrentPrice is read in its own (Exalted-equiv) unit and never inverted.
    """
    out: list[Observation] = []
    for it in raw.get("Items", []):
        price = it.get("CurrentPrice")
        if price is None or price <= 0:
            continue
        qty = it.get("CurrentQuantity")
        volume = float(qty) if qty is not None else None
        api_id = str(it["ApiId"])
        out.append(Observation(
            item_id=api_id, league_id=league_id, src_ts=src_ts, wall_ts=src_ts,
            name=it.get("Text") or api_id, category="currency", is_currency_pair=True,
            log_price=to_log_price(float(price)), price_exalt=float(price),
            volume=volume, vol_daily=None, stock=None, doi=None,
            liq_tier=tier_from_volume(volume), trade_id=None, valid=True))
    return out
