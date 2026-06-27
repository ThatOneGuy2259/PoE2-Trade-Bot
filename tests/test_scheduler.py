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

    async def get_currency_overview(self, league):
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
        async def get_currency_overview(self, league): raise RuntimeError("down")
    cb = CircuitBreaker(threshold=1)
    sent = []
    async def notify(ev): sent.append(ev)
    n = await poll_once(s, _Boom(), DetectConfig(), now_ts=1, breaker=cb, notify=notify)
    assert n == -1 and cb.is_open is True
    assert {"health": "source_down"} in sent       # new breaker trip notifies the health channel
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
