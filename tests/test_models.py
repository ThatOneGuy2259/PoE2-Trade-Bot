from poe2bot.models import LiquidityTier, Observation, AlertEvent


def test_tier_gate():
    assert LiquidityTier.LOW.gate == 0.3       # discounted but non-zero (rare items still scored)
    assert LiquidityTier.MED.gate == 0.6
    assert LiquidityTier.HIGH.gate == 1.0


def test_observation_is_frozen():
    obs = Observation(item_id="divine", league_id="L", src_ts=1, wall_ts=1, name="Divine Orb",
                      category="currency", is_currency_pair=True, log_price=0.0, price_exalt=1.0,
                      volume=500.0, vol_daily=None, stock=10.0, doi=0.02,
                      liq_tier=LiquidityTier.MED, trade_id="t", valid=True)
    assert obs.price_exalt == 1.0
    import dataclasses
    try:
        obs.price_exalt = 2.0  # type: ignore
        assert False, "should be frozen"
    except dataclasses.FrozenInstanceError:
        pass
