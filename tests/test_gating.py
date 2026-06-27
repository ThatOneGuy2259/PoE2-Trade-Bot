from poe2bot.detector.gating import (is_fresh, QualityConfig, hard_block_reason,
                                     has_min_samples, in_early_league)
from poe2bot.models import Observation, LiquidityTier

def _obs(tier=LiquidityTier.MED, valid=True, gap=False, src_ts=1000):
    return Observation(item_id="x", league_id="L", src_ts=src_ts, wall_ts=src_ts, name="X",
                       category="currency", is_currency_pair=True, log_price=0.0, price_exalt=1.0,
                       volume=500.0, vol_daily=None, stock=None, doi=None, liq_tier=tier,
                       trade_id=None, valid=valid, gap=gap)

def test_is_fresh():
    assert is_fresh(1000, 900, now_ts=1000, max_age_s=10800) is True
    assert is_fresh(1000, 1000, now_ts=1000, max_age_s=10800) is False   # unchanged ts
    assert is_fresh(1000, 900, now_ts=1000 + 99999, max_age_s=10800) is False  # too old

def test_hard_block_low_liquidity():
    cfg = QualityConfig()
    r = hard_block_reason(_obs(tier=LiquidityTier.LOW), prev_src_ts=900, now_ts=1000, cfg=cfg)
    assert r == "low_liquidity"

def test_hard_block_stale_and_invalid():
    cfg = QualityConfig()
    assert hard_block_reason(_obs(src_ts=1000), prev_src_ts=1000, now_ts=1000, cfg=cfg) == "stale"
    assert hard_block_reason(_obs(valid=False), prev_src_ts=900, now_ts=1000, cfg=cfg) == "invalid_or_gap"

def test_hard_block_ignores_sample_count():
    cfg = QualityConfig(min_samples=12)
    # a healthy MED observation is NOT hard-blocked regardless of how little history exists
    assert hard_block_reason(_obs(), prev_src_ts=900, now_ts=1000, cfg=cfg) is None

def test_has_min_samples():
    cfg = QualityConfig(min_samples=12)
    assert has_min_samples(3, cfg) is False
    assert has_min_samples(50, cfg) is True

def test_in_early_league():
    cfg = QualityConfig(early_league_mute_s=172800)
    assert in_early_league(now_ts=1000, league_started_at=0, cfg=cfg) is True
    assert in_early_league(now_ts=200000, league_started_at=0, cfg=cfg) is False
