from pathlib import Path
from poe2bot.store import Store
from poe2bot.scheduler import poll_once
from poe2bot.detector.engine import DetectConfig
from poe2bot.health import CircuitBreaker

FIX = Path(__file__).parent / "fixtures"


class _SeqClient:
    """Returns real-shaped currency payloads with a jumping divine price, plus league meta."""
    def __init__(self, prices):
        self._prices = prices
        self._i = 0

    async def get_currency_overview(self, league):
        p = self._prices[min(self._i, len(self._prices) - 1)]
        self._i += 1
        return {"CurrentPage": 1, "Pages": 1, "Total": 1,
                "Items": [{"ApiId": "divine", "Text": "Divine Orb",
                           "CurrentPrice": p, "CurrentQuantity": 1500}]}

    async def get_league_meta(self, league):
        return {"DivinePrice": 250.0, "ChaosDivinePrice": 1.0}


async def test_end_to_end_pipeline_fires(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    sent = []
    async def notify(ev): sent.append(ev)
    cb = CircuitBreaker()
    cfg = DetectConfig()
    # 15 flat polls seed the baseline, then a +40% move confirmed over 2 polls -> a JUMP fires.
    # src_ts = the now_ts we pass (poe2scout has no epoch); keep it current so data is "fresh".
    prices = [1.0] * 15 + [1.4, 1.4]
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
