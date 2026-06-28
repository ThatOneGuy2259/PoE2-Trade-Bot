import json
from pathlib import Path
from poe2bot.models import Anchor, LiquidityTier
from poe2bot.sources.normalize import (normalize_currency, normalize_uniques,
                                        tier_from_volume, _daily_volume)

FIX = Path(__file__).parent / "fixtures"

def test_tier_from_volume():
    # thresholds are for DAILY volume: LOW <5k, MED 5k-100k, HIGH >=100k
    assert tier_from_volume(None) == LiquidityTier.LOW
    assert tier_from_volume(4000) == LiquidityTier.LOW
    assert tier_from_volume(50000) == LiquidityTier.MED
    assert tier_from_volume(200000) == LiquidityTier.HIGH

def test_normalize_currency_maps_fields():
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    anchor = Anchor(divine_exalt=250.0, chaos_divine=1.0)
    obs = normalize_currency(raw, "L", anchor, src_ts=1782000000)
    by_id = {o.item_id: o for o in obs}
    divine = by_id["divine"]
    assert divine.price_exalt == 1.0
    assert abs(divine.log_price) < 1e-9
    assert divine.liq_tier == LiquidityTier.HIGH      # daily volume 200000 -> HIGH
    assert divine.volume == 200000.0                  # volume from PriceLogs (daily), not snapshot
    assert divine.stock == 1500.0                     # snapshot CurrentQuantity kept as stock
    assert divine.is_currency_pair is True
    assert divine.name == "Divine Orb"                # name comes from the Text field
    assert divine.trade_id is None                    # currency items have no trade id
    assert by_id["exalted"].liq_tier == LiquidityTier.LOW   # daily volume 40 -> LOW
    # CurrentPrice read in its own unit, never inverted (0.004 stays 0.004)
    assert by_id["exalted"].price_exalt == 0.004
    assert divine.category == "currency"                # default category tag


def test_normalize_currency_tags_requested_category():
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    anchor = Anchor(divine_exalt=250.0, chaos_divine=1.0)
    obs = normalize_currency(raw, "L", anchor, src_ts=1, category="fragments")
    assert obs and all(o.category == "fragments" for o in obs)   # tagged with the requested api_id


def test_daily_volume_skips_null_entries():
    # uniques can carry null PriceLog gaps; the latest non-null Quantity still resolves
    assert _daily_volume({"PriceLogs": [None, {"Price": 1.0, "Quantity": 42}, None]}) == 42.0
    assert _daily_volume({"PriceLogs": [None, None]}) is None
    assert _daily_volume({}) is None


def test_normalize_uniques_maps_fields():
    anchor = Anchor(divine_exalt=250.0, chaos_divine=1.0)
    raw = {"Items": [
        {"UniqueItemId": 229, "Text": "Bluetongue Shortsword", "Name": "Bluetongue",
         "CategoryApiId": "weapon", "Type": "Shortsword", "CurrentPrice": 1000,  # int -> float
         "CurrentQuantity": 3,
         "PriceLogs": [None, {"Price": 1000.0, "Time": "2026-06-27T00:00:00", "Quantity": 7}]},
        {"Name": "no price"},                                  # no CurrentPrice -> skipped
        {"UniqueItemId": 5, "Text": "free", "CurrentPrice": 0},  # price 0 -> skipped
        {"Text": "no id", "CurrentPrice": 10.0},                # no UniqueItemId -> skipped
    ]}
    obs = normalize_uniques(raw, "L", anchor, src_ts=1, category="weapon")
    assert len(obs) == 1
    o = obs[0]
    assert o.item_id == "unique-229"                           # keyed on UniqueItemId, prefixed
    assert o.name == "Bluetongue Shortsword"                   # Text preferred
    assert o.category == "weapon" and o.is_currency_pair is False
    assert o.price_exalt == 1000.0 and isinstance(o.price_exalt, float)
    assert o.volume == 7.0                                     # latest non-null PriceLog quantity
    assert o.stock == 3.0 and o.trade_id is None and o.valid is True
