from __future__ import annotations
import aiosqlite
from .models import Observation, AlertEvent, LiquidityTier

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS league (
  league_id TEXT PRIMARY KEY, ggg_key TEXT, display_name TEXT,
  started_at_real INTEGER, divine_exalt REAL, chaos_divine REAL, active INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS obs (
  item_id TEXT, league_id TEXT, src_ts INTEGER, wall_ts INTEGER, name TEXT, category TEXT,
  is_currency_pair INTEGER, log_price REAL, price_exalt REAL, volume REAL, vol_daily REAL,
  stock REAL, doi REAL, liq_tier INTEGER, trade_id TEXT, valid INTEGER, gap INTEGER,
  PRIMARY KEY (item_id, src_ts));
CREATE INDEX IF NOT EXISTS ix_obs_item_ts ON obs(item_id, src_ts);
CREATE TABLE IF NOT EXISTS detector_state (
  item_id TEXT PRIMARY KEY, mu_frozen REAL, n_obs INTEGER DEFAULT 0,
  last_fire_up_ts INTEGER DEFAULT 0, last_fire_dn_ts INTEGER DEFAULT 0,
  recovery_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS alert_log (
  alert_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, src_ts INTEGER, cls TEXT,
  direction TEXT, magnitude REAL, baseline REAL, current REAL, severity REAL,
  fired INTEGER, suppressed_reason TEXT);
"""

_STATE_DEFAULTS = {"mu_frozen": None, "n_obs": 0, "last_fire_up_ts": 0,
                   "last_fire_dn_ts": 0, "recovery_count": 0}


class Store:
    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    @classmethod
    async def open(cls, path: str) -> "Store":
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.executescript(_SCHEMA)
        await db.commit()
        return cls(db)

    async def close(self) -> None:
        await self._db.close()

    async def set_setting(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await self._db.commit()

    async def get_setting(self, key: str) -> str | None:
        cur = await self._db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def upsert_league(self, league_id, ggg_key, display_name, started_at_real,
                            divine_exalt, chaos_divine) -> None:
        await self._db.execute(
            "INSERT INTO league(league_id,ggg_key,display_name,started_at_real,divine_exalt,chaos_divine)"
            " VALUES(?,?,?,?,?,?) ON CONFLICT(league_id) DO UPDATE SET "
            " ggg_key=excluded.ggg_key, display_name=excluded.display_name,"
            " started_at_real=excluded.started_at_real, divine_exalt=excluded.divine_exalt,"
            " chaos_divine=excluded.chaos_divine",
            (league_id, ggg_key, display_name, started_at_real, divine_exalt, chaos_divine))
        await self._db.commit()

    async def set_active_league(self, league_id: str) -> None:
        await self._db.execute("UPDATE league SET active=0")
        await self._db.execute("UPDATE league SET active=1 WHERE league_id=?", (league_id,))
        await self._db.commit()

    async def get_active_league(self) -> str | None:
        cur = await self._db.execute("SELECT league_id FROM league WHERE active=1 LIMIT 1")
        row = await cur.fetchone()
        return row["league_id"] if row else None

    async def get_league_started_at(self, league_id: str) -> int:
        cur = await self._db.execute(
            "SELECT started_at_real FROM league WHERE league_id=?", (league_id,))
        row = await cur.fetchone()
        return int(row["started_at_real"]) if row and row["started_at_real"] is not None else 0

    async def insert_observation(self, obs: Observation) -> bool:
        try:
            await self._db.execute(
                "INSERT INTO obs(item_id,league_id,src_ts,wall_ts,name,category,is_currency_pair,"
                "log_price,price_exalt,volume,vol_daily,stock,doi,liq_tier,trade_id,valid,gap) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (obs.item_id, obs.league_id, obs.src_ts, obs.wall_ts, obs.name, obs.category,
                 int(obs.is_currency_pair), obs.log_price, obs.price_exalt, obs.volume,
                 obs.vol_daily, obs.stock, obs.doi, int(obs.liq_tier), obs.trade_id,
                 int(obs.valid), int(obs.gap)))
            await self._db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def last_observation(self, item_id: str) -> Observation | None:
        cur = await self._db.execute(
            "SELECT * FROM obs WHERE item_id=? ORDER BY src_ts DESC LIMIT 1", (item_id,))
        row = await cur.fetchone()
        return _row_to_obs(row) if row else None

    async def price_log_window(self, item_id: str, since_ts: int) -> list[float]:
        cur = await self._db.execute(
            "SELECT log_price FROM obs WHERE item_id=? AND src_ts>=? AND valid=1 ORDER BY src_ts",
            (item_id, since_ts))
        return [r["log_price"] for r in await cur.fetchall()]

    async def volume_window(self, item_id: str, since_ts: int) -> list[float]:
        cur = await self._db.execute(
            "SELECT volume FROM obs WHERE item_id=? AND src_ts>=? AND valid=1 "
            "AND volume IS NOT NULL ORDER BY src_ts", (item_id, since_ts))
        return [r["volume"] for r in await cur.fetchall()]

    async def get_detector_state(self, item_id: str) -> dict:
        cur = await self._db.execute("SELECT * FROM detector_state WHERE item_id=?", (item_id,))
        row = await cur.fetchone()
        if not row:
            return dict(_STATE_DEFAULTS)
        return {k: row[k] for k in _STATE_DEFAULTS}

    async def update_detector_state(self, item_id: str, **fields) -> None:
        cur = await self._db.execute("SELECT item_id FROM detector_state WHERE item_id=?", (item_id,))
        if not await cur.fetchone():
            await self._db.execute("INSERT INTO detector_state(item_id) VALUES(?)", (item_id,))
        for k, v in fields.items():
            if k not in _STATE_DEFAULTS:
                raise KeyError(f"unknown detector_state field {k}")
            await self._db.execute(f"UPDATE detector_state SET {k}=? WHERE item_id=?", (v, item_id))
        await self._db.commit()

    async def record_alert(self, event: AlertEvent, fired: bool,
                           suppressed_reason: str | None, src_ts: int) -> None:
        await self._db.execute(
            "INSERT INTO alert_log(item_id,src_ts,cls,direction,magnitude,baseline,current,"
            "severity,fired,suppressed_reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (event.item_id, src_ts, event.cls, event.direction, event.magnitude,
             event.baseline, event.current, event.severity, int(fired), suppressed_reason))
        await self._db.commit()

    async def prune(self, now_ts: int, raw_keep_days: int = 14) -> None:
        cutoff = now_ts - raw_keep_days * 86400
        await self._db.execute("DELETE FROM obs WHERE src_ts < ?", (cutoff,))
        await self._db.commit()


def _row_to_obs(row) -> Observation:
    return Observation(
        item_id=row["item_id"], league_id=row["league_id"], src_ts=row["src_ts"],
        wall_ts=row["wall_ts"], name=row["name"], category=row["category"],
        is_currency_pair=bool(row["is_currency_pair"]), log_price=row["log_price"],
        price_exalt=row["price_exalt"], volume=row["volume"], vol_daily=row["vol_daily"],
        stock=row["stock"], doi=row["doi"], liq_tier=LiquidityTier(row["liq_tier"]),
        trade_id=row["trade_id"], valid=bool(row["valid"]), gap=bool(row["gap"]))
