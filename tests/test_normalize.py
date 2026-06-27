import json
from pathlib import Path
from poe2bot.models import Anchor, LiquidityTier
from poe2bot.sources.normalize import normalize_currency, tier_from_volume

FIX = Path(__file__).parent / "fixtures"

def test_tier_from_volume():
    assert tier_from_volume(None) == LiquidityTier.LOW
    assert tier_from_volume(50) == LiquidityTier.LOW
    assert tier_from_volume(500) == LiquidityTier.MED
    assert tier_from_volume(5000) == LiquidityTier.HIGH

def test_normalize_currency_maps_fields():
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    anchor = Anchor(divine_exalt=250.0, chaos_divine=1.0)
    obs = normalize_currency(raw, "L", anchor, src_ts=1782000000)
    by_id = {o.item_id: o for o in obs}
    divine = by_id["divine"]
    assert divine.price_exalt == 1.0
    assert abs(divine.log_price) < 1e-9
    assert divine.liq_tier == LiquidityTier.HIGH      # qty 1500
    assert divine.is_currency_pair is True
    assert by_id["exalted"].liq_tier == LiquidityTier.LOW   # qty 40
    # currentPrice read in its own unit, never inverted (0.004 stays 0.004)
    assert by_id["exalted"].price_exalt == 0.004
