import json
from pathlib import Path
from poe2bot.store import Store
from poe2bot.scheduler import poll_once, extract_src_ts, extract_anchor
from poe2bot.detector.engine import DetectConfig
from poe2bot.health import CircuitBreaker

FIX = Path(__file__).parent / "fixtures"

class _StubClient:
    def __init__(self, payload): self._p = payload
    async def get_currency_overview(self, league): return self._p

def test_extract_helpers():
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    assert extract_src_ts(raw) == 1782000000
    assert extract_anchor(raw).divine_exalt >= 1.0

async def test_poll_once_dedups_stale_epoch(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.upsert_league("L", "L", "L", 0, 1.0, 1.0)
    await s.set_active_league("L")
    await s.set_setting("league", "L")
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    sent = []
    async def notify(ev): sent.append(ev)
    cb = CircuitBreaker()
    n1 = await poll_once(s, _StubClient(raw), DetectConfig(), now_ts=1782000000, breaker=cb, notify=notify)
    # second poll, same epoch -> stale, returns 0, no new fires
    n2 = await poll_once(s, _StubClient(raw), DetectConfig(), now_ts=1782000050, breaker=cb, notify=notify)
    assert n2 == 0
    await s.close()

async def test_poll_once_records_failure_and_health(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    class _Boom:
        async def get_currency_overview(self, league): raise RuntimeError("down")
    cb = CircuitBreaker(threshold=1)
    sent = []
    async def notify(ev): sent.append(ev)
    n = await poll_once(s, _Boom(), DetectConfig(), now_ts=1, breaker=cb, notify=notify)
    assert n == -1 and cb.is_open is True
    assert {"health": "source_down"} in sent       # new breaker trip notifies the health channel
    await s.close()

def test_clamp_anchor():
    from poe2bot.scheduler import _clamp_anchor
    assert _clamp_anchor(260.0, 250.0) == 260.0          # plausible -> accepted
    assert _clamp_anchor(5000.0, 250.0) == 250.0         # 20x jump -> rejected, keep prev
    assert _clamp_anchor(260.0, None) == 260.0           # no prior -> accept
