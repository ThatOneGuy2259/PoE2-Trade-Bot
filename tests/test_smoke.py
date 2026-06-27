import json, math
from pathlib import Path
from poe2bot.store import Store
from poe2bot.scheduler import poll_once
from poe2bot.detector.engine import DetectConfig
from poe2bot.health import CircuitBreaker
from poe2bot.models import Observation, LiquidityTier

FIX = Path(__file__).parent / "fixtures"

class _SeqClient:
    """Returns payloads with incrementing epochs and a jumping divine price."""
    def __init__(self, prices):
        self._prices = prices; self._i = 0
    async def get_currency_overview(self, league):
        p = self._prices[min(self._i, len(self._prices)-1)]; self._i += 1
        return {"epoch": 1000 + self._i,
                "items": [{"apiId": "divine", "name": "Divine Orb",
                           "currentPrice": p, "currentQuantity": 1500, "tradeId": "divine"}]}

async def test_end_to_end_pipeline_fires(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    await s.upsert_league("L", "L", "L", 0, 1.0, 1.0); await s.set_active_league("L")
    sent = []
    async def notify(ev): sent.append(ev)
    cb = CircuitBreaker()
    cfg = DetectConfig()
    # 15 flat polls seed the baseline, then a +40% move confirmed over 2 polls -> a JUMP fires.
    # _SeqClient sets epoch = 1001 + k, so we align now_ts to the epoch (keeps data "fresh").
    prices = [1.0]*15 + [1.4, 1.4]
    client = _SeqClient(prices)
    for k in range(len(prices)):
        await poll_once(s, client, cfg, now_ts=1001 + k, breaker=cb, notify=notify)
    rows = await s.price_log_window("divine", since_ts=0)
    assert len(rows) >= 15                                  # ledger persisted
    fired = [e for e in sent if not isinstance(e, dict)]
    assert len(fired) >= 1 and fired[0].cls == "JUMP"       # a real alert reached notify
    cur = await s._db.execute("SELECT COUNT(*) c FROM alert_log WHERE fired=1")
    assert (await cur.fetchone())["c"] >= 1                 # and was recorded as fired
    await s.close()
