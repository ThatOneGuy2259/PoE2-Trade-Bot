import pytest
from poe2bot.store import Store
from poe2bot.models import Observation, LiquidityTier

def _obs(item_id="divine", src_ts=100, price=1.0, vol=500.0, league="L"):
    return Observation(item_id=item_id, league_id=league, src_ts=src_ts, wall_ts=src_ts,
                       name="Divine Orb", category="currency", is_currency_pair=True,
                       log_price=0.0, price_exalt=price, volume=vol, vol_daily=None,
                       stock=10.0, doi=0.02, liq_tier=LiquidityTier.MED, trade_id="t", valid=True)

async def test_open_and_settings(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "Rise of the Abyssal")
    assert await s.get_setting("league") == "Rise of the Abyssal"
    assert await s.get_setting("missing") is None
    await s.close()

async def test_league_started_at(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert await s.get_league_started_at("L") == 0          # unknown -> 0
    await s.upsert_league("L", "L", "L", 1700000000, 250.0, 1.0)
    await s.set_active_league("L")
    assert await s.get_league_started_at("L") == 1700000000
    await s.close()

async def test_insert_dedup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert await s.insert_observation(_obs(src_ts=100)) is True
    assert await s.insert_observation(_obs(src_ts=100)) is False  # same (item, ts)
    assert await s.insert_observation(_obs(src_ts=200)) is True
    await s.close()

async def test_windows_ordered(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    for ts, p, v in [(100, 1.0, 500.0), (200, 1.1, 480.0), (300, 1.2, 200.0)]:
        import math
        o = _obs(src_ts=ts, price=p, vol=v)
        o = Observation(**{**o.__dict__, "log_price": math.log(p)})
        await s.insert_observation(o)
    prices = await s.price_log_window("divine", since_ts=150)
    vols = await s.volume_window("divine", since_ts=150)
    assert len(prices) == 2 and prices[0] < prices[1]
    assert vols == [480.0, 200.0]
    await s.close()

async def test_detector_state_roundtrip(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    st = await s.get_detector_state("divine")
    assert st["n_obs"] == 0 and st["mu_frozen"] is None
    await s.update_detector_state("divine", mu_frozen=0.5, n_obs=3, last_fire_dn_ts=999)
    st = await s.get_detector_state("divine")
    assert st["mu_frozen"] == 0.5 and st["n_obs"] == 3 and st["last_fire_dn_ts"] == 999
    await s.close()
