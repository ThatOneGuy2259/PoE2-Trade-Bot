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


async def test_poll_once_bootstraps_league_row(tmp_path):
    """First poll bootstraps the league row; second poll does NOT overwrite started_at."""
    import copy
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    src_ts_1 = int(raw["epoch"])

    # Second payload: different epoch so the dedup gate doesn't short-circuit
    raw2 = copy.deepcopy(raw)
    raw2["epoch"] = src_ts_1 + 3600

    sent = []
    async def notify(ev): sent.append(ev)
    cb = CircuitBreaker()
    now_ts_1 = src_ts_1          # wall-clock aligned with epoch for freshness
    now_ts_2 = src_ts_1 + 7200  # 2 hours later

    # Poll 1: no league row exists → bootstrap with now_ts_1
    await poll_once(s, _StubClient(raw), DetectConfig(), now_ts=now_ts_1, breaker=cb, notify=notify)
    started = await s.get_league_started_at("L")
    assert started == now_ts_1, f"expected {now_ts_1}, got {started}"

    # Poll 2: different epoch, later now_ts → started_at must stay at now_ts_1
    await poll_once(s, _StubClient(raw2), DetectConfig(), now_ts=now_ts_2, breaker=cb, notify=notify)
    started_after = await s.get_league_started_at("L")
    assert started_after == now_ts_1, f"bootstrap overwritten: expected {now_ts_1}, got {started_after}"

    await s.close()
