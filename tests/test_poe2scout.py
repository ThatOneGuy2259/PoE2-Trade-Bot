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
    def __init__(self, payload): self._p = payload; self.last_headers = None
    def get(self, url, headers=None, params=None):
        self.last_headers = headers
        return _FakeResp(self._p)

async def test_get_leagues_sends_ua():
    payload = json.loads((FIX / "poe2scout_leagues.json").read_text())
    sess = _FakeSession(payload)
    client = Poe2ScoutClient(sess, ua="poe2bot/test (contact: me)")
    leagues = await client.get_leagues()
    assert leagues == ["Rise of the Abyssal", "Standard"]
    assert "poe2bot/test" in sess.last_headers["User-Agent"]
