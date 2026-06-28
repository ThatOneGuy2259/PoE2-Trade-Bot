import pytest
from poe2bot.store import Store
import discord
from poe2bot.bot import (LeagueService, setleague_logic, status_text, set_categories_logic,
                         set_threshold_logic, price_text, topmovers_text,
                         set_alert_channel_logic, set_health_channel_logic, resolve_channel_id,
                         ItemService, filter_item_choices, filter_category_choices, CATEGORIES,
                         sync_target_guilds, sync_commands, pollnow_logic)
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
    assert "/pollnow" in msg                      # nudge the user to fetch immediately
    assert await s.get_setting("league") == "Standard"
    with pytest.raises(ValueError):
        await setleague_logic(s, ["Standard"], "Nonexistent League")
    await s.close()


async def test_pollnow_logic_no_league(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    calls = []
    async def poll_now(): calls.append(1); return 0
    msg = await pollnow_logic(s, poll_now)
    assert "setleague" in msg.lower()
    assert calls == []                            # must NOT poll when no league is set
    await s.close()


async def test_pollnow_logic_source_down(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "Runes of Aldur")
    async def poll_now(): return -1               # poll_once returns -1 on source failure
    msg = await pollnow_logic(s, poll_now)
    assert "failed" in msg.lower()
    await s.close()


async def test_pollnow_logic_success_reports_counts(tmp_path):
    import math
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "Runes of Aldur")
    # simulate a poll that ingested 2 items at the latest src_ts and fired 1 alert
    async def poll_now():
        for iid in ("divine", "exalted"):
            await s.insert_observation(Observation(
                item_id=iid, league_id="L", src_ts=100, wall_ts=100, name=iid,
                category="currency", is_currency_pair=True, log_price=math.log(2.0),
                price_exalt=2.0, volume=1.0, vol_daily=None, stock=None, doi=None,
                liq_tier=LiquidityTier.HIGH, trade_id=None, valid=True))
        return 1
    msg = await pollnow_logic(s, poll_now)
    assert "Runes of Aldur" in msg
    assert "2 item" in msg and "1 alert" in msg   # 2 items ingested, 1 alert fired
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
    # a fraction is required: a whole-number footgun (20 == "2000%") is rejected
    with pytest.raises(ValueError):
        await set_threshold_logic(s, "currency", 20)
    with pytest.raises(ValueError):
        await set_threshold_logic(s, "currency", 0)
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


# --- autocomplete: items (store-backed; covers currency + uniques) ---------

def _item_obs(item_id, name, league="Standard", ts=100, currency=True):
    import math
    return Observation(item_id=item_id, league_id=league, src_ts=ts, wall_ts=ts, name=name,
                       category="currency" if currency else "weapon", is_currency_pair=currency,
                       log_price=math.log(2.0), price_exalt=2.0, volume=1.0, vol_daily=None,
                       stock=None, doi=None, liq_tier=LiquidityTier.HIGH, trade_id=None, valid=True)


async def test_item_service_lists_observed_items_incl_uniques(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    svc = ItemService(s, ttl_s=100)
    assert await svc.available(now_ts=0) == []                 # no league -> empty
    await s.set_setting("league", "Standard")
    assert await svc.available(now_ts=0) == []                 # league set, no obs yet
    await s.insert_observation(_item_obs("divine", "Divine Orb"))
    await s.insert_observation(_item_obs("unique-42", "Bluetongue Shortsword", currency=False))
    await s.insert_observation(_item_obs("other", "Other", league="HC"))   # different league
    pairs = await svc.refresh("Standard", now_ts=200)
    assert ("Divine Orb", "divine") in pairs
    assert ("Bluetongue Shortsword", "unique-42") in pairs      # uniques now searchable by name
    assert all(name != "Other" for name, _ in pairs)           # other-league item excluded
    await s.close()


async def test_item_service_latest_name_and_league_switch(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    svc = ItemService(s, ttl_s=100)
    await s.set_setting("league", "Standard")
    await s.insert_observation(_item_obs("divine", "Divine Orb", ts=100))
    await s.insert_observation(_item_obs("divine", "Divine Orb v2", ts=300))   # newer name
    assert ("Divine Orb v2", "divine") in await svc.refresh("Standard", now_ts=400)
    await s.insert_observation(_item_obs("hc-item", "HC Item", league="Hardcore"))
    await s.set_setting("league", "Hardcore")                  # league change -> re-read even in ttl
    pairs = await svc.available(now_ts=410)
    assert ("HC Item", "hc-item") in pairs and all(i != "divine" for _, i in pairs)
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

def test_sync_target_guilds():
    # explicit DISCORD_GUILD_ID wins, ignoring joined list
    assert sync_target_guilds(999, [1, 2]) == [999]
    # unset -> every joined guild (auto-detect, no config needed)
    assert sync_target_guilds(None, [1, 2]) == [1, 2]
    # unset and not in any guild -> empty (caller falls back to a global sync)
    assert sync_target_guilds(None, []) == []


class _FakeResp:
    status = 403
    reason = "Forbidden"


class _FakeTree:
    """Records CommandTree calls; raises HTTPException for guilds in `fail`."""
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.copied, self.guild_syncs, self.global_syncs, self.cleared = [], [], 0, 0

    def copy_global_to(self, guild): self.copied.append(guild.id)

    async def sync(self, guild=None):
        if guild is None:
            self.global_syncs += 1
            return
        if guild.id in self.fail:
            raise discord.HTTPException(_FakeResp(), "missing access")
        self.guild_syncs.append(guild.id)

    def clear_commands(self, guild=None): self.cleared += 1


async def test_sync_commands_guild_success_clears_globals():
    t = _FakeTree()
    r = await sync_commands(t, [10, 20], guild_id=None)       # auto-detect both joined guilds
    assert r == {"mode": "guild", "synced": [10, 20], "failed": []}
    assert t.copied == [10, 20] and t.guild_syncs == [10, 20]
    assert t.cleared == 1 and t.global_syncs == 1             # globals cleared + pushed empty


async def test_sync_commands_bad_guild_keeps_globals():
    # the dangerous case: an explicit but wrong/inaccessible id must NOT strand the bot
    t = _FakeTree(fail={999})
    r = await sync_commands(t, [1, 2], guild_id=999)
    assert r["synced"] == [] and r["failed"] == [999]
    assert t.cleared == 0 and t.global_syncs == 0             # globals left intact


async def test_sync_commands_no_targets_falls_back_global():
    t = _FakeTree()
    r = await sync_commands(t, [], guild_id=None)             # in no guild, no id set
    assert r["mode"] == "global"
    assert t.global_syncs == 1 and t.cleared == 0 and t.guild_syncs == []


def test_categories_constant_shape():
    ids = {api_id for api_id, *_ in CATEGORIES}
    assert {"currency", "fragments", "runes", "essences"} <= ids          # currency-family
    assert {"weapon", "armour", "accessory"} <= ids                       # uniques (Phase 2B)
    assert all(isinstance(a, str) and isinstance(l, str) and fam in {"currency", "uniques"}
               for a, l, fam in CATEGORIES)


def test_category_family():
    from poe2bot.categories import category_family
    assert category_family("currency") == "currency"
    assert category_family("weapon") == "uniques"
    assert category_family("not-a-real-category") is None                 # unknown -> None, no raise


def test_filter_category_choices():
    # single token: matches api_id or label, case-insensitive
    opts = filter_category_choices("frag")
    assert opts == [("fragments", "fragments")]
    assert filter_category_choices("Soul") == [("ultimatum", "ultimatum")]      # label match
    assert filter_category_choices("") == [(a, a) for a, *_ in CATEGORIES]       # all, value=id
    # comma list: completes the LAST token, preserves earlier ones, skips chosen
    opts = filter_category_choices("currency,fr")
    assert ("currency,fragments", "currency,fragments") in opts
    assert all(not v.endswith(",currency") for _, v in opts)                     # 'currency' excluded
    assert filter_category_choices("currency,fragments,").count(("currency,fragments,currency",
                                                                 "currency,fragments,currency")) == 0
