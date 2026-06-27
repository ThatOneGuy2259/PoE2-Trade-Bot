from __future__ import annotations
from dataclasses import dataclass
from ..models import Observation, LiquidityTier


@dataclass(frozen=True)
class QualityConfig:
    max_age_s: int = 10800          # ~3x assumed hourly refresh
    min_samples: int = 12
    early_league_mute_s: int = 172800  # 48h


def is_fresh(src_ts: int, prev_src_ts: int | None, now_ts: int, max_age_s: int) -> bool:
    if prev_src_ts is not None and src_ts == prev_src_ts:
        return False
    return (now_ts - src_ts) <= max_age_s


def in_early_league(now_ts: int, league_started_at: int, cfg: QualityConfig) -> bool:
    return (now_ts - league_started_at) < cfg.early_league_mute_s


def has_min_samples(n_samples: int, cfg: QualityConfig) -> bool:
    return n_samples >= cfg.min_samples


def hard_block_reason(obs: Observation, prev_src_ts: int | None, now_ts: int,
                      cfg: QualityConfig) -> str | None:
    """Always-on gate: blocks every fire including the fast-path. No sample-count check."""
    if not obs.valid or obs.gap:
        return "invalid_or_gap"
    if not is_fresh(obs.src_ts, prev_src_ts, now_ts, cfg.max_age_s):
        return "stale"
    if obs.liq_tier == LiquidityTier.LOW:
        return "low_liquidity"
    return None
