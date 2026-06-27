import pytest
from poe2bot.store import Store
from poe2bot.bot import LeagueService, setleague_logic, status_text

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
