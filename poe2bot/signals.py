from __future__ import annotations
import math
import statistics


def to_log_price(price_exalt: float) -> float:
    return math.log(max(price_exalt, 1e-9))


def median(xs: list[float]) -> float:
    if not xs:
        raise ValueError("median of empty sequence")
    return float(statistics.median(xs))


def mad(xs: list[float], med: float | None = None) -> float:
    if not xs:
        raise ValueError("mad of empty sequence")
    m = median(xs) if med is None else med
    return float(statistics.median([abs(x - m) for x in xs]))


def robust_z(x: float, med: float, mad_: float, eps: float = 1e-9) -> float:
    return 0.6745 * (x - med) / max(mad_, eps)


def pct_from_log(log_now: float, log_ref: float) -> float:
    return math.exp(log_now - log_ref) - 1.0


def relative_drop(current: float, baseline: float, eps: float = 1e-9) -> float:
    return (baseline - current) / max(baseline, eps)


def wfs_phase1(price_exalt: float, gate: float, divine_exalt: float,
               volume_24h: float, eps: float = 1e-9) -> float:
    realizable = price_exalt * gate / max(divine_exalt, eps)
    absorption = max(volume_24h, 0.0) / 24.0
    return realizable * (absorption ** 0.7)
