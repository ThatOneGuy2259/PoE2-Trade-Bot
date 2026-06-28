from __future__ import annotations
from ..models import Observation, LiquidityTier, Anchor
from ..signals import to_log_price


def tier_from_volume(volume: float | None, low: float = 5000.0, med: float = 100000.0) -> LiquidityTier:
    # Thresholds are for DAILY traded volume (PriceLogs.Quantity), not the small live
    # snapshot count. Calibrated against the live PoE2 currency distribution: the rare,
    # high-value items (Mirror ~900/day, Hinekora's Lock ~3k/day) fall LOW.
    if volume is None or volume < low:
        return LiquidityTier.LOW
    if volume < med:
        return LiquidityTier.MED
    return LiquidityTier.HIGH


def _daily_volume(it: dict) -> float | None:
    """Most recent daily traded quantity from the PriceLogs series (the real activity
    signal), falling back to None when the item has no logs."""
    for entry in reversed(it.get("PriceLogs") or []):
        if entry is None:                 # some series carry null gaps; skip them
            continue
        q = entry.get("Quantity")
        if q is not None:
            return float(q)
    return None


def normalize_currency(raw: dict, league_id: str, anchor: Anchor, src_ts: int,
                       category: str = "currency") -> list[Observation]:
    """Normalize a poe2scout Currencies/ByCategory response into Observations.

    Live API fields are PascalCase: Items[].ApiId / Text / CurrentPrice / CurrentQuantity.
    There is no trade id on currency items (trade_id stays None — deep links are best-effort).
    CurrentPrice is read in its own (Exalted-equiv) unit and never inverted. `category` is the
    api_id we fetched; tagging each Observation with it (rather than the per-item CategoryApiId)
    keeps obs.category exactly equal to the `thr:<category>` threshold key.
    """
    out: list[Observation] = []
    for it in raw.get("Items", []):
        price = it.get("CurrentPrice")
        if price is None or price <= 0:
            continue
        # volume = real DAILY traded quantity (drives tiering + demand); stock = the live
        # snapshot count (CurrentQuantity), kept for Phase-2 supply-vs-flow analysis.
        volume = _daily_volume(it)
        snap = it.get("CurrentQuantity")
        stock = float(snap) if snap is not None else None
        api_id = str(it["ApiId"])
        out.append(Observation(
            item_id=api_id, league_id=league_id, src_ts=src_ts, wall_ts=src_ts,
            name=it.get("Text") or api_id, category=category, is_currency_pair=True,
            log_price=to_log_price(float(price)), price_exalt=float(price),
            volume=volume, vol_daily=volume, stock=stock, doi=None,
            liq_tier=tier_from_volume(volume), trade_id=None, valid=True))
    return out
