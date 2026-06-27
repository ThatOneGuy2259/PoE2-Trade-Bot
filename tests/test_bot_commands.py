import pytest
from poe2bot.store import Store
from poe2bot.bot import (LeagueService, setleague_logic, status_text, set_categories_logic,
                         set_threshold_logic, price_text, topmovers_text,
                         set_alert_channel_logic, set_health_channel_logic, resolve_channel_id)
from poe2bot.models import Observation, LiquidityTier


async def test_set_alert_channel_persists_and_resolves(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    # before any /setchannel: resolver falls back to the env default
    assert await resolve_channel_id(s, "alert_channel_id", fallback=42) == 42
    assert await resolve_channel_id(s, "alert_channel_id", fallback=None) is None
    # /setchannel sets it live; resolver now returns the stored value, ignoring the fallback
    msg = await set_alert_channel_logic(s, 999)
    assert "999" in msg
    assert await s.get_setting("alert_channel_id") == "999"
    assert await resolve_channel_id(s, "alert_channel_id", fallback=42) == 999
    await s.close()


async def test_set_health_channel_persists(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await set_health_channel_logic(s, 555)
    assert await resolve_channel_id(s, "health_channel_id", fallback=None) == 555
    await s.close()


async def test_status_shows_channel(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    txt = await status_text(s)
    assert "Alert channel" in txt                    # shown even when unset
    await set_alert_channel_logic(s, 777)
    assert "777" in await status_text(s)              # reflects the live setting
    await s.close()

class _StubClient:
    def __init__(self, leagues): self._l = leagues; self.calls = 0
    async def get_leagues(self): self.calls += 1; return list(self._l)

async def test_league_service_caches():
    svc = LeagueService(_StubClient(["A", "B"]), ttl_s=100)
    assert await svc.available(now_ts=0) == ["A", "B"]
    await svc.available(now_ts=50)            # within ttl -> cached
    assert svc._client.calls == 1
    await svc.available(now_ts=200)           # expired -> refetch
    assert svc._client.calls == 2

async def test_setleague_validates(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    msg = await setleague_logic(s, ["Rise of the Abyssal", "Standard"], "Standard")
    assert "Standard" in msg
    assert await s.get_setting("league") == "Standard"
    with pytest.raises(ValueError):
        await setleague_logic(s, ["Standard"], "Nonexistent League")
    await s.close()

async def test_status_text(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "Standard")
    txt = await status_text(s)
    assert "Standard" in txt
    await s.close()


async def test_categories_and_threshold(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await set_categories_logic(s, ["currency", "uniques"])
    assert await s.get_setting("categories") == "currency,uniques"
    await set_threshold_logic(s, "currency", 0.2)
    assert await s.get_setting("thr:currency") == "0.2"
    await s.close()


async def test_price_text_no_data_then_data(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert "no data" in (await price_text(s, "divine")).lower()
    import math
    await s.insert_observation(Observation(
        item_id="divine", league_id="L", src_ts=1, wall_ts=1, name="Divine Orb",
        category="currency", is_currency_pair=True, log_price=math.log(1.0), price_exalt=1.0,
        volume=1500.0, vol_daily=None, stock=None, doi=None, liq_tier=LiquidityTier.HIGH,
        trade_id="divine", valid=True))
    txt = await price_text(s, "divine")
    assert "Divine Orb" in txt and "HIGH" in txt
    await s.close()


async def test_topmovers_empty_during_warmup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert "no movers" in (await topmovers_text(s, 5)).lower()
    await s.close()
