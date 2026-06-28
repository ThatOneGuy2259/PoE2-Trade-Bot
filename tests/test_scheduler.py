import json
from pathlib import Path
from poe2bot.store import Store
from poe2bot.scheduler import poll_once, _clamp_anchor
from poe2bot.detector.engine import DetectConfig
from poe2bot.health import CircuitBreaker

FIX = Path(__file__).parent / "fixtures"


class _StubClient:
    """Stub poe2scout client: returns a fixed currency payload + league meta."""
    def __init__(self, payload, divine=250.0):
        self._p = payload
        self._divine = divine

    async def get_currency_overview(self, league, category="currency"):
        return self._p

    async def get_league_meta(self, league):
        return {"DivinePrice": self._divine, "ChaosDivinePrice": 1.0}


def test_clamp_anchor():
    assert _clamp_anchor(260.0, 250.0) == 260.0          # plausible -> accepted
    assert _clamp_anchor(5000.0, 250.0) == 250.0         # 20x jump -> rejected, keep prev
    assert _clamp_anchor(260.0, None) == 260.0           # no prior -> accept


async def test_poll_once_ingests_and_sets_anchor(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    sent = []
    async def notify(ev): sent.append(ev)
    cb = CircuitBreaker()
    # cold ledger: observations are stored but nothing fires (no baseline yet)
    n = await poll_once(s, _StubClient(raw, divine=250.0), DetectConfig(),
                        now_ts=1_000_000, breaker=cb, notify=notify)
    assert n == 0 and sent == []
    assert len(await s.price_log_window("divine", since_ts=0)) == 1   # divine stored
    assert await s.get_setting("anchor_divine") == "250.0"            # anchor from DivinePrice
    assert await s.get_setting("last_poll_ts") == "1000000"
    await s.close()


async def test_poll_once_records_failure_and_health(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    class _Boom:
        # meta succeeds (so the anchor is fine); every category fetch fails -> all-categories-failed
        async def get_league_meta(self, league): return {"DivinePrice": 250.0, "ChaosDivinePrice": 1.0}
        async def get_currency_overview(self, league, category="currency"): raise RuntimeError("down")
    cb = CircuitBreaker(threshold=1)
    sent = []
    async def notify(ev): sent.append(ev)
    n = await poll_once(s, _Boom(), DetectConfig(), now_ts=1, breaker=cb, notify=notify)
    assert n == -1 and cb.is_open is True
    assert {"health": "source_down"} in sent       # new breaker trip notifies the health channel
    assert await s.get_setting("last_poll_ts") is None   # failed poll does NOT advance the timestamp
    await s.close()


async def test_poll_once_meta_failure_is_systemic(tmp_path):
    """A league-meta failure (anchor undefined) is systemic -> -1, before any category fetch."""
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    class _MetaBoom:
        async def get_league_meta(self, league): raise RuntimeError("meta down")
        async def get_currency_overview(self, league, category="currency"): return {"Items": []}
    cb = CircuitBreaker(threshold=1)
    sent = []
    async def notify(ev): sent.append(ev)
    n = await poll_once(s, _MetaBoom(), DetectConfig(), now_ts=1, breaker=cb, notify=notify)
    assert n == -1 and {"health": "source_down"} in sent
    await s.close()


class _MultiCatClient:
    """Records the categories requested; returns one item per category (or raises for `bad`)."""
    def __init__(self, bad=()):
        self.requested = []
        self._bad = set(bad)

    async def get_league_meta(self, league):
        return {"DivinePrice": 250.0, "ChaosDivinePrice": 1.0}

    async def get_currency_overview(self, league, category="currency"):
        self.requested.append(category)
        if category in self._bad:
            raise RuntimeError("boom")
        return {"Items": [{"ApiId": f"{category}-item", "Text": f"{category} item",
                           "CurrentPrice": 5.0, "CurrentQuantity": 10,
                           "PriceLogs": [{"Price": 5.0, "Time": "2026-06-27T00:00:00",
                                          "Quantity": 200000}]}]}


async def test_poll_once_scans_configured_categories(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    await s.set_setting("categories", "currency, fragments , currency")   # dupe + whitespace
    c = _MultiCatClient()
    cb = CircuitBreaker()
    async def notify(ev): pass
    await poll_once(s, c, DetectConfig(), now_ts=1_000_000, breaker=cb, notify=notify)
    assert c.requested == ["currency", "fragments"]                       # deduped, ordered
    # each category's item was ingested and tagged with its category
    cur = await s._db.execute("SELECT item_id, category FROM obs ORDER BY item_id")
    rows = {r["item_id"]: r["category"] for r in await cur.fetchall()}
    assert rows == {"currency-item": "currency", "fragments-item": "fragments"}
    await s.close()


async def test_poll_once_skips_one_bad_category(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    await s.set_setting("categories", "currency,fragments")
    c = _MultiCatClient(bad={"fragments"})                                # fragments fetch raises
    cb = CircuitBreaker()
    async def notify(ev): pass
    n = await poll_once(s, c, DetectConfig(), now_ts=1_000_000, breaker=cb, notify=notify)
    assert n == 0                                                          # not -1: one category survived
    cur = await s._db.execute("SELECT item_id FROM obs")
    ids = {r["item_id"] for r in await cur.fetchall()}
    assert ids == {"currency-item"}                                       # only the good category ingested
    assert await s.get_setting("last_poll_ts") == "1000000"               # poll completed
    await s.close()


async def test_poll_once_bootstraps_league_row(tmp_path):
    """First poll bootstraps the league row; a later poll does NOT overwrite started_at."""
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    sent = []
    async def notify(ev): sent.append(ev)
    cb = CircuitBreaker()
    now_1, now_2 = 1_000_000, 1_007_200   # 2 hours apart

    await poll_once(s, _StubClient(raw), DetectConfig(), now_ts=now_1, breaker=cb, notify=notify)
    assert await s.get_league_started_at("L") == now_1            # bootstrapped to first-poll time

    await poll_once(s, _StubClient(raw), DetectConfig(), now_ts=now_2, breaker=cb, notify=notify)
    assert await s.get_league_started_at("L") == now_1            # not overwritten on later polls
    await s.close()


class _FamilyClient:
    """Stub that records which endpoint each category hit (dispatch-by-family check)."""
    def __init__(self): self.cur = []; self.uniq = []
    async def get_league_meta(self, league): return {"DivinePrice": 250.0, "ChaosDivinePrice": 1.0}
    async def get_currency_overview(self, league, category="currency"):
        self.cur.append(category)
        return {"Items": [{"ApiId": f"{category}-x", "Text": "c", "CurrentPrice": 5.0,
                           "CurrentQuantity": 1, "PriceLogs": [{"Price": 5.0, "Quantity": 100000}]}]}
    async def get_uniques_overview(self, league, category):
        self.uniq.append(category)
        return {"Items": [{"UniqueItemId": 42, "Text": "Bluetongue", "CurrentPrice": 1000.0,
                           "CurrentQuantity": 3, "PriceLogs": [None, {"Price": 1000.0, "Quantity": 7}]}]}


async def test_poll_once_dispatches_uniques_by_family(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    await s.set_setting("categories", "currency,weapon")
    c = _FamilyClient()
    async def notify(ev): pass
    await poll_once(s, c, DetectConfig(), now_ts=1_000_000, breaker=CircuitBreaker(), notify=notify)
    assert c.cur == ["currency"] and c.uniq == ["weapon"]        # routed to the right endpoint each
    cur = await s._db.execute("SELECT item_id, category, is_currency_pair FROM obs ORDER BY item_id")
    rows = {r["item_id"]: (r["category"], r["is_currency_pair"]) for r in await cur.fetchall()}
    assert rows == {"currency-x": ("currency", 1), "unique-42": ("weapon", 0)}
    await s.close()


async def test_poll_once_skips_unknown_category(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    await s.set_setting("categories", "currency,bogus")
    c = _MultiCatClient()
    async def notify(ev): pass
    n = await poll_once(s, c, DetectConfig(), now_ts=1_000_000, breaker=CircuitBreaker(), notify=notify)
    assert c.requested == ["currency"]                          # 'bogus' is never fetched
    assert n == 0
    await s.close()


async def test_poll_once_all_unknown_returns_zero_no_alarm(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    await s.set_setting("categories", "bogus1,bogus2")
    c = _MultiCatClient()
    cb = CircuitBreaker(threshold=1)
    sent = []
    async def notify(ev): sent.append(ev)
    n = await poll_once(s, c, DetectConfig(), now_ts=1, breaker=cb, notify=notify)
    assert n == 0 and c.requested == []                         # nothing fetched
    assert {"health": "source_down"} not in sent and cb.is_open is False   # not a source-down alarm
    await s.close()
