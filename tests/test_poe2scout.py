import json
from pathlib import Path
import pytest
from poe2bot.sources.poe2scout import Poe2ScoutClient

FIX = Path(__file__).parent / "fixtures"

class _FakeResp:
    def __init__(self, payload): self._p = payload
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def raise_for_status(self): pass
    async def json(self): return self._p

class _FakeSession:
    def __init__(self, payload): self._p = payload; self.last_headers = None; self.last_url = None
    def get(self, url, headers=None, params=None):
        self.last_headers = headers
        self.last_url = url
        return _FakeResp(self._p)

async def test_get_leagues_sends_ua_and_current_first():
    payload = json.loads((FIX / "poe2scout_leagues.json").read_text())
    sess = _FakeSession(payload)
    client = Poe2ScoutClient(sess, ua="poe2bot/test (contact: me)")
    leagues = await client.get_leagues()
    # IsCurrent league ("Rise of the Abyssal") is ordered first
    assert leagues == ["Rise of the Abyssal", "Standard"]
    assert "poe2bot/test" in sess.last_headers["User-Agent"]

async def test_get_current_league_and_meta():
    payload = json.loads((FIX / "poe2scout_leagues.json").read_text())
    client = Poe2ScoutClient(_FakeSession(payload), ua="poe2bot/test")
    assert await client.get_current_league() == "Rise of the Abyssal"
    meta = await client.get_league_meta("Rise of the Abyssal")
    assert meta["DivinePrice"] == 250.0

async def test_get_currency_overview_parses_items():
    payload = json.loads((FIX / "poe2scout_currency.json").read_text())
    sess = _FakeSession(payload)
    client = Poe2ScoutClient(sess, ua="poe2bot/test", req_delay_s=0)
    raw = await client.get_currency_overview("Rise of the Abyssal")
    assert raw["Total"] == 2 and len(raw["Items"]) == 2
    assert {i["ApiId"] for i in raw["Items"]} == {"divine", "exalted"}
    assert "Category=currency" in str(sess.last_url)          # default category


async def test_get_currency_overview_threads_category():
    payload = json.loads((FIX / "poe2scout_currency.json").read_text())
    sess = _FakeSession(payload)
    client = Poe2ScoutClient(sess, ua="poe2bot/test", req_delay_s=0)
    await client.get_currency_overview("Rise of the Abyssal", "fragments")
    assert "Category=fragments" in str(sess.last_url)         # category threads into the querystring
    assert "/Currencies/ByCategory" in str(sess.last_url)     # currency-family segment


async def test_get_uniques_overview_hits_uniques_segment():
    sess = _FakeSession({"CurrentPage": 1, "Pages": 1, "Total": 1, "Items": [{"UniqueItemId": 1}]})
    client = Poe2ScoutClient(sess, ua="poe2bot/test", req_delay_s=0)
    raw = await client.get_uniques_overview("Rise of the Abyssal", "weapon")
    assert raw["Total"] == 1 and len(raw["Items"]) == 1
    assert "/Uniques/ByCategory" in str(sess.last_url)        # uniques segment, not Currencies
    assert "Category=weapon" in str(sess.last_url)
