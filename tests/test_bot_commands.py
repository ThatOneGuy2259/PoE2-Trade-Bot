import pytest
from poe2bot.store import Store
from poe2bot.bot import (LeagueService, setleague_logic, status_text, set_categories_logic,
                         set_threshold_logic, price_text, topmovers_text,
                         set_alert_channel_logic, set_health_channel_logic, resolve_channel_id,
                         ItemService, filter_item_choices, filter_category_choices, CATEGORIES)
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


# --- autocomplete: items ---------------------------------------------------

class _StubItemClient:
    def __init__(self, items): self._items = items; self.calls = 0
    async def get_currency_overview(self, league): self.calls += 1; return {"Items": self._items}


async def test_item_service_caches_and_keys_on_league(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    items = [{"ApiId": "divine", "Text": "Divine Orb"},
             {"ApiId": "exalted", "Text": "Exalted Orb"},
             {"ApiId": "noname"}]                      # missing Text -> falls back to ApiId
    c = _StubItemClient(items)
    svc = ItemService(c, s, ttl_s=100)
    # no league set -> empty, and no fetch attempted
    assert await svc.available(now_ts=0) == []
    assert c.calls == 0
    await s.set_setting("league", "Standard")
    pairs = await svc.available(now_ts=0)
    assert ("Divine Orb", "divine") in pairs
    assert ("noname", "noname") in pairs               # Text fallback -> ApiId
    assert c.calls == 1
    await svc.available(now_ts=50)                      # within ttl -> cached
    assert c.calls == 1
    await svc.available(now_ts=200)                     # ttl expired -> refetch
    assert c.calls == 2
    await s.set_setting("league", "Hardcore")          # league change -> refetch even in ttl
    await svc.available(now_ts=210)
    assert c.calls == 3
    await s.close()


def test_filter_item_choices():
    pairs = [("Divine Orb", "divine"), ("Exalted Orb", "exalted"), ("Fancy", "xyz")]
    assert filter_item_choices(pairs, "div") == [("Divine Orb", "divine")]      # name match
    assert filter_item_choices(pairs, "xyz") == [("Fancy", "xyz")]              # id match
    assert filter_item_choices(pairs, "ORB") == [("Divine Orb", "divine"),
                                                 ("Exalted Orb", "exalted")]    # case-insensitive
    assert filter_item_choices(pairs, "") == pairs                             # empty -> all
    many = [(f"Item {i}", f"id{i}") for i in range(40)]
    assert len(filter_item_choices(many, "item")) == 25                        # capped at 25


# --- autocomplete: categories (preset, comma-aware) ------------------------

def test_categories_constant_shape():
    ids = {api_id for api_id, _ in CATEGORIES}
    assert {"currency", "fragments", "runes", "essences"} <= ids
    assert all(isinstance(a, str) and isinstance(l, str) for a, l in CATEGORIES)


def test_filter_category_choices():
    # single token: matches api_id or label, case-insensitive
    opts = filter_category_choices("frag")
    assert opts == [("fragments", "fragments")]
    assert filter_category_choices("Soul") == [("ultimatum", "ultimatum")]      # label match
    assert filter_category_choices("") == [(a, a) for a, _ in CATEGORIES]        # all, value=id
    # comma list: completes the LAST token, preserves earlier ones, skips chosen
    opts = filter_category_choices("currency,fr")
    assert ("currency,fragments", "currency,fragments") in opts
    assert all(not v.endswith(",currency") for _, v in opts)                     # 'currency' excluded
    assert filter_category_choices("currency,fragments,").count(("currency,fragments,currency",
                                                                 "currency,fragments,currency")) == 0
