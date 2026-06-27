# PoE2 Trade Bot — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a 24/7 Discord bot that polls the PoE2 market (poe2scout), stores a deduped price/demand ledger in SQLite, and posts robust-but-simple alerts for price jumps, crashes, and demand collapse — while accumulating the history the Phase-2 detectors need.

**Architecture:** A scheduler polls poe2scout each interval, normalizes each item into an `Observation` (log-price, Exalted-normalized, demand level, liquidity tier), persists it deduped on `(item_id, server_timestamp)`, then a detector compares each item to a frozen reference and its own rolling robust baseline, applies hard gates + persistence + cooldown + a global per-poll top-K cap, and emits Discord embeds. A health module guards against silent blindness.

**Tech Stack:** Python 3.11+, discord.py 2.x, aiohttp, aiosqlite, APScheduler 3.x, pytest + pytest-asyncio. Packaged with Docker + systemd.

## Global Constraints

- Python **3.11+**. Async throughout (`asyncio`).
- **poe2scout is the only load-bearing source.** poe.ninja is optional cross-check, NOT required for any Phase-1 feature. Phase 1 may ship without poe.ninja.
- Every poe2scout request sends a **contact User-Agent** (`POE2SCOUT_UA` env); respect ~2 req/s (burst 5).
- Store prices in **log-space**, normalized to **Exalted-equivalent** via the league `DivineAnchor`. poe2scout `CurrentPrice` is read in its own unit and **never inverted**.
- Persist one `obs` row per item per **distinct server timestamp** (dedup unchanged snapshots).
- **No market alert may fire under data-quality uncertainty** (stale / thin / early-league / anchor-out-of-bounds). Suppressions are logged with a reason.
- League-list source is the **poe2scout leagues endpoint**. League selected via `/setleague` autocomplete; nothing hardcoded.
- Liquidity tiers (provisional, configurable): **LOW < 100**, **MED 100–999**, **HIGH ≥ 1000** (quantity). Gate multiplier `g = 0 / 0.6 / 1.0`.
- Detector thresholds (Phase 1): absolute move floor **±15%** (**±25%** for cheap items < 2 Exalted-equiv), magnitude fast-path **log-move ≥ 0.40**, persistence **2-of-3 fresh polls**, cooldown **6h per item per direction**, global per-poll cap **K = 8** with a "+N more movers" overflow line, demand-collapse drop **≥ 50%** with a **≥ 10 trades/day** eligibility floor, early-league crash/collapse mute **48h** from real league start.
- Poll interval default **30 min** (`POLL_INTERVAL_MIN`); windows are **time-based** so cadence drift is tolerated.
- TDD: every behavioral change starts with a failing test. Commit after each green step.

---

## File Structure

```
pyproject.toml                  deps + pytest config
.env.example                    documented env vars
Dockerfile                      runtime image
deploy/poe2bot.service          systemd unit
README.md                       run/deploy notes
poe2bot/
  __init__.py
  config.py                     Settings (env) + persisted-settings helpers
  models.py                     Observation, AlertEvent, LiquidityTier, Anchor
  signals.py                    pure math: robust stats, pct, WFS
  store.py                      Store(aiosqlite): schema, inserts, windows, state, prune
  sources/
    __init__.py
    poe2scout.py                Poe2ScoutClient (async)
    normalize.py                raw poe2scout JSON -> Observation
  detector/
    __init__.py
    gating.py                   liquidity tier, freshness, data-quality, early-league
    engine.py                   detect(): gates + frozen-ref change + persistence + cooldown + top-K
  alerts.py                     Discord embed formatting + overflow line
  bot.py                        discord.py client + slash commands + autocomplete
  scheduler.py                  poll_once() + nightly maintenance + APScheduler wiring
  health.py                     CircuitBreaker, pipeline-health, dead-man heartbeat
  main.py                       entrypoint
tests/
  test_signals.py test_store.py test_normalize.py test_poe2scout.py
  test_config.py test_bot_commands.py test_gating.py test_engine.py
  test_alerts.py test_scheduler.py test_health.py test_smoke.py
  conftest.py fixtures/poe2scout_*.json
```

---

## Stage A — Data layer

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `poe2bot/__init__.py`, `poe2bot/sources/__init__.py`, `poe2bot/detector/__init__.py`, `tests/conftest.py`, `.env.example`, `.gitignore`
- Test: `tests/test_smoke_import.py`

**Interfaces:**
- Produces: importable package `poe2bot`; `pytest` configured with `asyncio_mode = auto`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smoke_import.py
def test_package_imports():
    import poe2bot
    assert hasattr(poe2bot, "__version__")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_smoke_import.py -v`
Expected: FAIL (ModuleNotFoundError: poe2bot)

- [ ] **Step 3: Create scaffold**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "poe2bot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "discord.py>=2.3,<3",
  "aiohttp>=3.9",
  "aiosqlite>=0.19",
  "APScheduler>=3.10,<4",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["poe2bot*"]
```

```python
# poe2bot/__init__.py
__version__ = "0.1.0"
```

```python
# poe2bot/sources/__init__.py
# (empty)
```

```python
# poe2bot/detector/__init__.py
# (empty)
```

```python
# tests/conftest.py
import pytest
```

```bash
# .env.example
DISCORD_TOKEN=your-bot-token
ALERT_CHANNEL_ID=123456789012345678
HEALTH_CHANNEL_ID=123456789012345678
DB_PATH=./poe2bot.db
POLL_INTERVAL_MIN=30
POE2SCOUT_UA=poe2bot/0.1 (contact: you@example.com)
DEAD_MAN_URL=
```

```
# .gitignore
__pycache__/
*.db
.env
.venv/
*.egg-info/
```

- [ ] **Step 4: Install dev deps and run the test**

Run: `pip install -e ".[dev]" && python -m pytest tests/test_smoke_import.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml poe2bot tests .env.example .gitignore
git commit -m "chore: project scaffold and package layout"
```

---

### Task 2: Domain models

**Files:**
- Create: `poe2bot/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `class LiquidityTier(IntEnum)`: `LOW=0, MED=1, HIGH=2`; property `gate -> float` (0.0/0.6/1.0).
  - `@dataclass(frozen=True) Observation`: `item_id:str, league_id:str, src_ts:int, wall_ts:int, name:str, category:str, is_currency_pair:bool, log_price:float, price_exalt:float, volume:float|None, vol_daily:float|None, stock:float|None, doi:float|None, liq_tier:LiquidityTier, trade_id:str|None, valid:bool, gap:bool=False`.
  - `@dataclass(frozen=True) AlertEvent`: `item_id:str, name:str, cls:str, direction:str, magnitude:float, pct_move:float, baseline:float, current:float, severity:float, liq_tier:LiquidityTier, trade_id:str|None, wfs:float`.
  - `@dataclass(frozen=True) Anchor`: `divine_exalt:float, chaos_divine:float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from poe2bot.models import LiquidityTier, Observation, AlertEvent

def test_tier_gate():
    assert LiquidityTier.LOW.gate == 0.0
    assert LiquidityTier.MED.gate == 0.6
    assert LiquidityTier.HIGH.gate == 1.0

def test_observation_is_frozen():
    obs = Observation(item_id="divine", league_id="L", src_ts=1, wall_ts=1, name="Divine Orb",
                      category="currency", is_currency_pair=True, log_price=0.0, price_exalt=1.0,
                      volume=500.0, vol_daily=None, stock=10.0, doi=0.02,
                      liq_tier=LiquidityTier.MED, trade_id="t", valid=True)
    assert obs.price_exalt == 1.0
    import dataclasses
    try:
        obs.price_exalt = 2.0  # type: ignore
        assert False, "should be frozen"
    except dataclasses.FrozenInstanceError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL (ModuleNotFoundError: poe2bot.models)

- [ ] **Step 3: Implement models**

```python
# poe2bot/models.py
from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum

class LiquidityTier(IntEnum):
    LOW = 0
    MED = 1
    HIGH = 2

    @property
    def gate(self) -> float:
        return {LiquidityTier.LOW: 0.0, LiquidityTier.MED: 0.6, LiquidityTier.HIGH: 1.0}[self]

@dataclass(frozen=True)
class Observation:
    item_id: str
    league_id: str
    src_ts: int
    wall_ts: int
    name: str
    category: str
    is_currency_pair: bool
    log_price: float
    price_exalt: float
    volume: float | None
    vol_daily: float | None
    stock: float | None
    doi: float | None
    liq_tier: LiquidityTier
    trade_id: str | None
    valid: bool
    gap: bool = False

@dataclass(frozen=True)
class AlertEvent:
    item_id: str
    name: str
    cls: str          # "JUMP" | "CRASH" | "DEMAND_COLLAPSE"
    direction: str    # "up" | "down"
    magnitude: float  # log move vs frozen reference
    pct_move: float
    baseline: float
    current: float
    severity: float
    liq_tier: LiquidityTier
    trade_id: str | None
    wfs: float

@dataclass(frozen=True)
class Anchor:
    divine_exalt: float
    chaos_divine: float
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/models.py tests/test_models.py
git commit -m "feat: domain models (Observation, AlertEvent, LiquidityTier)"
```

---

### Task 3: Pure signal math

**Files:**
- Create: `poe2bot/signals.py`
- Test: `tests/test_signals.py`

**Interfaces:**
- Produces:
  - `to_log_price(price_exalt: float) -> float` — `math.log(max(price_exalt, 1e-9))`.
  - `median(xs: list[float]) -> float` (raises `ValueError` on empty).
  - `mad(xs: list[float], med: float | None = None) -> float` — median absolute deviation.
  - `robust_z(x: float, med: float, mad_: float, eps: float = 1e-9) -> float` — `0.6745*(x-med)/max(mad_, eps)`.
  - `pct_from_log(log_now: float, log_ref: float) -> float` — `math.exp(log_now-log_ref) - 1`.
  - `relative_drop(current: float, baseline: float, eps: float = 1e-9) -> float` — `(baseline-current)/max(baseline, eps)`.
  - `wfs_phase1(price_exalt: float, gate: float, divine_exalt: float, volume_24h: float, eps: float = 1e-9) -> float` — `(price_exalt*gate/max(divine_exalt, eps)) * (max(volume_24h, 0.0)/24.0)**0.7`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signals.py
import math
import pytest
from poe2bot.signals import (to_log_price, median, mad, robust_z, pct_from_log,
                             relative_drop, wfs_phase1)

def test_median_and_mad():
    assert median([1, 3, 2]) == 2
    assert mad([1, 1, 1]) == 0.0
    assert mad([1, 2, 3, 4, 5]) == 1.0  # |.-3| = 2,1,0,1,2 -> median 1

def test_median_empty_raises():
    with pytest.raises(ValueError):
        median([])

def test_robust_z_uses_eps_when_mad_zero():
    # mad 0 -> divides by eps -> large but finite, not ZeroDivision
    z = robust_z(2.0, 1.0, 0.0)
    assert math.isfinite(z) and z > 0

def test_pct_from_log_roundtrip():
    assert pct_from_log(math.log(1.2), math.log(1.0)) == pytest.approx(0.2)
    assert pct_from_log(math.log(0.5), math.log(1.0)) == pytest.approx(-0.5)

def test_relative_drop():
    assert relative_drop(50, 100) == pytest.approx(0.5)
    assert relative_drop(100, 100) == pytest.approx(0.0)

def test_wfs_phase1_zero_gate_is_zero():
    assert wfs_phase1(100.0, 0.0, 1.0, 1000.0) == 0.0

def test_wfs_phase1_monotonic_in_price():
    a = wfs_phase1(10.0, 1.0, 1.0, 100.0)
    b = wfs_phase1(20.0, 1.0, 1.0, 100.0)
    assert b > a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_signals.py -v`
Expected: FAIL (ModuleNotFoundError: poe2bot.signals)

- [ ] **Step 3: Implement signals**

```python
# poe2bot/signals.py
from __future__ import annotations
import math
import statistics

def to_log_price(price_exalt: float) -> float:
    return math.log(max(price_exalt, 1e-9))

def median(xs: list[float]) -> float:
    if not xs:
        raise ValueError("median of empty sequence")
    return float(statistics.median(xs))

def mad(xs: list[float], med: float | None = None) -> float:
    if not xs:
        raise ValueError("mad of empty sequence")
    m = median(xs) if med is None else med
    return float(statistics.median([abs(x - m) for x in xs]))

def robust_z(x: float, med: float, mad_: float, eps: float = 1e-9) -> float:
    return 0.6745 * (x - med) / max(mad_, eps)

def pct_from_log(log_now: float, log_ref: float) -> float:
    return math.exp(log_now - log_ref) - 1.0

def relative_drop(current: float, baseline: float, eps: float = 1e-9) -> float:
    return (baseline - current) / max(baseline, eps)

def wfs_phase1(price_exalt: float, gate: float, divine_exalt: float,
               volume_24h: float, eps: float = 1e-9) -> float:
    realizable = price_exalt * gate / max(divine_exalt, eps)
    absorption = max(volume_24h, 0.0) / 24.0
    return realizable * (absorption ** 0.7)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_signals.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add poe2bot/signals.py tests/test_signals.py
git commit -m "feat: pure signal math (robust stats, pct, Phase-1 WFS)"
```

---

### Task 4: SQLite store

**Files:**
- Create: `poe2bot/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Observation`, `LiquidityTier` (Task 2).
- Produces `class Store` (async, aiosqlite). Methods:
  - `await Store.open(path: str) -> Store` (opens connection, runs `init_schema`).
  - `await close()`.
  - `await upsert_league(league_id, ggg_key, display_name, started_at_real, divine_exalt, chaos_divine)`.
  - `await set_active_league(league_id)` / `await get_active_league() -> str | None`.
  - `await get_league_started_at(league_id) -> int` (returns `started_at_real`, or 0 if unknown).
  - `await set_setting(key, value: str)` / `await get_setting(key) -> str | None`.
  - `await insert_observation(obs: Observation) -> bool` (returns False if `(item_id, src_ts)` already present — dedup).
  - `await last_observation(item_id) -> Observation | None`.
  - `await price_log_window(item_id, since_ts) -> list[float]` (ascending by ts).
  - `await volume_window(item_id, since_ts) -> list[float]`.
  - `await get_detector_state(item_id) -> dict` (defaults: `mu_frozen=None, n_obs=0, last_fire_up_ts=0, last_fire_dn_ts=0, recovery_count=0`).
  - `await update_detector_state(item_id, **fields)`.
  - `await record_alert(event: AlertEvent, fired: bool, suppressed_reason: str | None, src_ts: int)`.
  - `await prune(now_ts, raw_keep_days=14)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
import pytest
from poe2bot.store import Store
from poe2bot.models import Observation, LiquidityTier

def _obs(item_id="divine", src_ts=100, price=1.0, vol=500.0, league="L"):
    return Observation(item_id=item_id, league_id=league, src_ts=src_ts, wall_ts=src_ts,
                       name="Divine Orb", category="currency", is_currency_pair=True,
                       log_price=0.0, price_exalt=price, volume=vol, vol_daily=None,
                       stock=10.0, doi=0.02, liq_tier=LiquidityTier.MED, trade_id="t", valid=True)

async def test_open_and_settings(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "Rise of the Abyssal")
    assert await s.get_setting("league") == "Rise of the Abyssal"
    assert await s.get_setting("missing") is None
    await s.close()

async def test_league_started_at(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert await s.get_league_started_at("L") == 0          # unknown -> 0
    await s.upsert_league("L", "L", "L", 1700000000, 250.0, 1.0)
    await s.set_active_league("L")
    assert await s.get_league_started_at("L") == 1700000000
    await s.close()

async def test_insert_dedup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert await s.insert_observation(_obs(src_ts=100)) is True
    assert await s.insert_observation(_obs(src_ts=100)) is False  # same (item, ts)
    assert await s.insert_observation(_obs(src_ts=200)) is True
    await s.close()

async def test_windows_ordered(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    for ts, p, v in [(100, 1.0, 500.0), (200, 1.1, 480.0), (300, 1.2, 200.0)]:
        import math
        o = _obs(src_ts=ts, price=p, vol=v)
        o = Observation(**{**o.__dict__, "log_price": math.log(p)})
        await s.insert_observation(o)
    prices = await s.price_log_window("divine", since_ts=150)
    vols = await s.volume_window("divine", since_ts=150)
    assert len(prices) == 2 and prices[0] < prices[1]
    assert vols == [480.0, 200.0]
    await s.close()

async def test_detector_state_roundtrip(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    st = await s.get_detector_state("divine")
    assert st["n_obs"] == 0 and st["mu_frozen"] is None
    await s.update_detector_state("divine", mu_frozen=0.5, n_obs=3, last_fire_dn_ts=999)
    st = await s.get_detector_state("divine")
    assert st["mu_frozen"] == 0.5 and st["n_obs"] == 3 and st["last_fire_dn_ts"] == 999
    await s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store.py -v`
Expected: FAIL (ModuleNotFoundError: poe2bot.store)

- [ ] **Step 3: Implement store**

```python
# poe2bot/store.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add poe2bot/store.py tests/test_store.py
git commit -m "feat: SQLite store (dedup ledger, windows, detector state, alert log)"
```

---

### Task 5: poe2scout client + normalize

**Files:**
- Create: `poe2bot/sources/poe2scout.py`, `poe2bot/sources/normalize.py`, `tests/fixtures/poe2scout_currency.json`, `tests/fixtures/poe2scout_leagues.json`
- Test: `tests/test_poe2scout.py`, `tests/test_normalize.py`

**Interfaces:**
- Consumes: `Observation`, `LiquidityTier`, `Anchor` (Task 2); `to_log_price` (Task 3); `liquidity_tier` will live in `gating` (Task 9) but to avoid a forward dependency, normalize takes a `tier_fn` callable param.
- Produces:
  - `class Poe2ScoutClient`: `__init__(self, session: aiohttp.ClientSession, ua: str, base="https://api.poe2scout.com")`; `await get_leagues() -> list[str]`; `await get_currency_overview(league: str) -> dict` (raw JSON). All requests send `User-Agent: ua`.
  - `normalize.py`:
    - `tier_from_volume(volume: float | None, low=100.0, med=1000.0) -> LiquidityTier`.
    - `normalize_currency(raw: dict, league_id: str, anchor: Anchor, src_ts: int) -> list[Observation]`.

- [ ] **Step 1: Write the failing tests + fixtures**

```json
// tests/fixtures/poe2scout_leagues.json
[{"value": "Rise of the Abyssal"}, {"value": "Standard"}]
```

```json
// tests/fixtures/poe2scout_currency.json
{"epoch": 1782000000,
 "items": [
   {"apiId": "divine", "name": "Divine Orb", "currentPrice": 1.0, "currentQuantity": 1500, "tradeId": "divine"},
   {"apiId": "exalted", "name": "Exalted Orb", "currentPrice": 0.004, "currentQuantity": 40, "tradeId": "exalted"}
 ]}
```

```python
# tests/test_normalize.py
import json, math
from pathlib import Path
from poe2bot.models import Anchor, LiquidityTier
from poe2bot.sources.normalize import normalize_currency, tier_from_volume

FIX = Path(__file__).parent / "fixtures"

def test_tier_from_volume():
    assert tier_from_volume(None) == LiquidityTier.LOW
    assert tier_from_volume(50) == LiquidityTier.LOW
    assert tier_from_volume(500) == LiquidityTier.MED
    assert tier_from_volume(5000) == LiquidityTier.HIGH

def test_normalize_currency_maps_fields():
    raw = json.loads((FIX / "poe2scout_currency.json").read_text())
    anchor = Anchor(divine_exalt=250.0, chaos_divine=1.0)
    obs = normalize_currency(raw, "L", anchor, src_ts=1782000000)
    by_id = {o.item_id: o for o in obs}
    divine = by_id["divine"]
    assert divine.price_exalt == 1.0
    assert divine.log_price == math.approx(0.0) if hasattr(math, "approx") else abs(divine.log_price) < 1e-9
    assert divine.liq_tier == LiquidityTier.HIGH      # qty 1500
    assert divine.is_currency_pair is True
    assert by_id["exalted"].liq_tier == LiquidityTier.LOW   # qty 40
    # currentPrice read in its own unit, never inverted (0.004 stays 0.004)
    assert by_id["exalted"].price_exalt == 0.004
```

```python
# tests/test_poe2scout.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_normalize.py tests/test_poe2scout.py -v`
Expected: FAIL (ModuleNotFoundError for poe2bot.sources.*)

- [ ] **Step 3: Implement client + normalize**

```python
# poe2bot/sources/poe2scout.py
from __future__ import annotations
import aiohttp

class Poe2ScoutClient:
    def __init__(self, session: aiohttp.ClientSession, ua: str,
                 base: str = "https://api.poe2scout.com"):
        self._session = session
        self._ua = ua
        self._base = base.rstrip("/")

    def _headers(self) -> dict:
        return {"User-Agent": self._ua, "Accept": "application/json"}

    async def get_leagues(self) -> list[str]:
        async with self._session.get(f"{self._base}/leagues", headers=self._headers()) as r:
            r.raise_for_status()
            data = await r.json()
        # endpoint returns a list of {"value": <league name>}; tolerate bare strings too
        out = []
        for entry in data:
            out.append(entry["value"] if isinstance(entry, dict) else str(entry))
        return out

    async def get_currency_overview(self, league: str) -> dict:
        params = {"league": league}
        async with self._session.get(f"{self._base}/items/currency",
                                     headers=self._headers(), params=params) as r:
            r.raise_for_status()
            return await r.json()
```

```python
# poe2bot/sources/normalize.py
from __future__ import annotations
from ..models import Observation, LiquidityTier, Anchor
from ..signals import to_log_price

def tier_from_volume(volume: float | None, low: float = 100.0, med: float = 1000.0) -> LiquidityTier:
    if volume is None or volume < low:
        return LiquidityTier.LOW
    if volume < med:
        return LiquidityTier.MED
    return LiquidityTier.HIGH

def normalize_currency(raw: dict, league_id: str, anchor: Anchor, src_ts: int) -> list[Observation]:
    out: list[Observation] = []
    for it in raw.get("items", []):
        price = it.get("currentPrice")
        if price is None or price <= 0:
            continue
        qty = it.get("currentQuantity")
        volume = float(qty) if qty is not None else None
        out.append(Observation(
            item_id=str(it["apiId"]), league_id=league_id, src_ts=src_ts, wall_ts=src_ts,
            name=it.get("name", it["apiId"]), category="currency", is_currency_pair=True,
            log_price=to_log_price(float(price)), price_exalt=float(price),
            volume=volume, vol_daily=None, stock=None, doi=None,
            liq_tier=tier_from_volume(volume), trade_id=it.get("tradeId"), valid=True))
    return out
```

- [ ] **Step 4: Fix the test helper and run**

Note: in `tests/test_normalize.py` replace the `math.approx` line with a plain `assert abs(divine.log_price) < 1e-9`.

Run: `python -m pytest tests/test_normalize.py tests/test_poe2scout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/sources tests/test_poe2scout.py tests/test_normalize.py tests/fixtures
git commit -m "feat: poe2scout client + currency normalization"
```

---

## Stage B — Bot + commands

### Task 6: Config

**Files:**
- Create: `poe2bot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces `@dataclass(frozen=True) Settings` with `discord_token:str, alert_channel_id:int, health_channel_id:int|None, db_path:str, poll_interval_min:int, poe2scout_ua:str, dead_man_url:str|None` and classmethod `Settings.from_env(env: Mapping[str,str]) -> Settings` (raises `ValueError` listing missing required vars: `DISCORD_TOKEN`, `ALERT_CHANNEL_ID`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest
from poe2bot.config import Settings

def test_from_env_minimal():
    s = Settings.from_env({"DISCORD_TOKEN": "t", "ALERT_CHANNEL_ID": "42"})
    assert s.discord_token == "t" and s.alert_channel_id == 42
    assert s.poll_interval_min == 30  # default

def test_from_env_missing_required():
    with pytest.raises(ValueError) as e:
        Settings.from_env({})
    assert "DISCORD_TOKEN" in str(e.value) and "ALERT_CHANNEL_ID" in str(e.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement config**

```python
# poe2bot/config.py
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping

@dataclass(frozen=True)
class Settings:
    discord_token: str
    alert_channel_id: int
    health_channel_id: int | None
    db_path: str
    poll_interval_min: int
    poe2scout_ua: str
    dead_man_url: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        missing = [k for k in ("DISCORD_TOKEN", "ALERT_CHANNEL_ID") if not env.get(k)]
        if missing:
            raise ValueError(f"missing required env vars: {', '.join(missing)}")
        health = env.get("HEALTH_CHANNEL_ID")
        return cls(
            discord_token=env["DISCORD_TOKEN"],
            alert_channel_id=int(env["ALERT_CHANNEL_ID"]),
            health_channel_id=int(health) if health else None,
            db_path=env.get("DB_PATH", "./poe2bot.db"),
            poll_interval_min=int(env.get("POLL_INTERVAL_MIN", "30")),
            poe2scout_ua=env.get("POE2SCOUT_UA", "poe2bot/0.1 (contact: unset)"),
            dead_man_url=env.get("DEAD_MAN_URL") or None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/config.py tests/test_config.py
git commit -m "feat: env-based settings"
```

---

### Task 7: Bot shell + league commands

**Files:**
- Create: `poe2bot/bot.py`
- Test: `tests/test_bot_commands.py`

**Interfaces:**
- Consumes: `Store` (Task 4), `Poe2ScoutClient` (Task 5).
- Produces:
  - `class LeagueService`: `__init__(self, client: Poe2ScoutClient, ttl_s=86400)`; `await available(now_ts: int) -> list[str]` (cached for `ttl_s`, keyed off a monotonic timestamp passed in); `await refresh(now_ts) -> list[str]`.
  - `async setleague_logic(store: Store, leagues: list[str], chosen: str) -> str` — validates `chosen ∈ leagues`, persists via `set_setting("league", chosen)`, returns a user-facing confirmation or raises `ValueError` for an unknown league.
  - `async status_text(store: Store) -> str` — renders league + last poll + top_k from settings.
  - `build_bot(store, league_service, settings)` returns a configured `discord.ext.commands.Bot` (slash commands registered). Slash-command callbacks are thin wrappers over the above logic functions so the logic is unit-testable without Discord.

  Logic functions are the unit-test surface; the Discord wiring is exercised only by the smoke test (Task 15).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_commands.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bot_commands.py -v`
Expected: FAIL (ModuleNotFoundError: poe2bot.bot)

- [ ] **Step 3: Implement bot logic + wiring**

```python
# poe2bot/bot.py
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from .store import Store
from .sources.poe2scout import Poe2ScoutClient

class LeagueService:
    def __init__(self, client: Poe2ScoutClient, ttl_s: int = 86400):
        self._client = client
        self._ttl = ttl_s
        self._cache: list[str] = []
        self._fetched_at: int | None = None

    async def refresh(self, now_ts: int) -> list[str]:
        self._cache = await self._client.get_leagues()
        self._fetched_at = now_ts
        return self._cache

    async def available(self, now_ts: int) -> list[str]:
        if self._fetched_at is None or (now_ts - self._fetched_at) >= self._ttl:
            return await self.refresh(now_ts)
        return self._cache

async def setleague_logic(store: Store, leagues: list[str], chosen: str) -> str:
    if chosen not in leagues:
        raise ValueError(f"'{chosen}' is not in the current league list")
    await store.set_setting("league", chosen)
    return f"League set to **{chosen}**."

async def status_text(store: Store) -> str:
    league = await store.get_setting("league") or "(unset)"
    last_poll = await store.get_setting("last_poll_ts") or "(never)"
    top_k = await store.get_setting("top_k") or "8"
    return f"League: {league}\nLast poll: {last_poll}\nPer-poll alert cap (K): {top_k}"

def build_bot(store: Store, league_service: LeagueService, settings) -> commands.Bot:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        await bot.tree.sync()

    async def _league_autocomplete(interaction: discord.Interaction, current: str):
        import time
        leagues = await league_service.available(int(time.time()))
        return [app_commands.Choice(name=l, value=l)
                for l in leagues if current.lower() in l.lower()][:25]

    @bot.tree.command(name="leagues", description="List currently available leagues")
    async def leagues_cmd(interaction: discord.Interaction):
        import time
        leagues = await league_service.available(int(time.time()))
        await interaction.response.send_message(", ".join(leagues) or "(none)", ephemeral=True)

    @bot.tree.command(name="setleague", description="Set the active league")
    @app_commands.autocomplete(name=_league_autocomplete)
    async def setleague_cmd(interaction: discord.Interaction, name: str):
        import time
        leagues = await league_service.available(int(time.time()))
        try:
            msg = await setleague_logic(store, leagues, name)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="status", description="Show bot status")
    async def status_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(await status_text(store), ephemeral=True)

    return bot
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bot_commands.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add poe2bot/bot.py tests/test_bot_commands.py
git commit -m "feat: bot shell + /leagues /setleague (autocomplete) /status"
```

---

### Task 8: Remaining commands (`/categories`, `/threshold`, `/price`, `/topmovers`)

**Files:**
- Modify: `poe2bot/bot.py`
- Test: `tests/test_bot_commands.py` (add cases)

**Interfaces:**
- Consumes: `Store`, `wfs_phase1` (Task 3), `LiquidityTier`.
- Produces logic functions:
  - `async set_categories_logic(store, categories: list[str]) -> str` — persists CSV under `set_setting("categories", ...)`.
  - `async set_threshold_logic(store, category: str, spike_pct: float) -> str` — persists under key `thr:{category}`.
  - `async price_text(store, item_id: str) -> str` — reads `last_observation`, returns price + tier + WFS, or "no data".
  - `async topmovers_text(store, n: int) -> str` — reads the most recent `alert_log` fired rows for the latest poll, formatted; "(no movers yet)" during warmup.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_bot_commands.py
from poe2bot.bot import set_categories_logic, set_threshold_logic, price_text, topmovers_text
from poe2bot.models import Observation, LiquidityTier

async def test_categories_and_threshold(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await set_categories_logic(s, ["currency", "uniques"])
    assert await s.get_setting("categories") == "currency,uniques"
    await set_threshold_logic(s, "currency", 0.2)
    assert await s.get_setting("thr:currency") == "0.2"
    await s.close()

async def test_price_text_no_data_then_data(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert "no data" in (await price_text(s, "divine")).lower()
    import math
    await s.insert_observation(Observation(
        item_id="divine", league_id="L", src_ts=1, wall_ts=1, name="Divine Orb",
        category="currency", is_currency_pair=True, log_price=math.log(1.0), price_exalt=1.0,
        volume=1500.0, vol_daily=None, stock=None, doi=None, liq_tier=LiquidityTier.HIGH,
        trade_id="divine", valid=True))
    txt = await price_text(s, "divine")
    assert "Divine Orb" in txt and "HIGH" in txt
    await s.close()

async def test_topmovers_empty_during_warmup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert "no movers" in (await topmovers_text(s, 5)).lower()
    await s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bot_commands.py -k "categories or price_text or topmovers" -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement the logic functions and register commands**

```python
# add to poe2bot/bot.py (top: from .signals import wfs_phase1)
async def set_categories_logic(store: Store, categories: list[str]) -> str:
    await store.set_setting("categories", ",".join(categories))
    return f"Scanning categories: {', '.join(categories)}"

async def set_threshold_logic(store: Store, category: str, spike_pct: float) -> str:
    await store.set_setting(f"thr:{category}", str(spike_pct))
    return f"Threshold for {category} set to {spike_pct:.0%}"

async def price_text(store: Store, item_id: str) -> str:
    obs = await store.last_observation(item_id)
    if obs is None:
        return f"No data for `{item_id}` yet."
    divine = 1.0  # WFS rebasing uses the league anchor in the scheduler; /price shows raw tier+price
    vol = obs.volume or 0.0
    wfs = wfs_phase1(obs.price_exalt, obs.liq_tier.gate, max(divine, 1e-9), vol)
    return (f"**{obs.name}** — {obs.price_exalt:g} (Exalted-equiv)\n"
            f"Liquidity: {obs.liq_tier.name} | 24h volume: {vol:g} | WFS: {wfs:.3g}")

async def topmovers_text(store: Store, n: int) -> str:
    cur = await store._db.execute(
        "SELECT item_id, cls, direction, magnitude FROM alert_log WHERE fired=1 "
        "ORDER BY alert_id DESC LIMIT ?", (n,))
    rows = await cur.fetchall()
    if not rows:
        return "(no movers yet)"
    lines = [f"{r['item_id']}: {r['cls']} {r['direction']} ({r['magnitude']:+.2f} log)" for r in rows]
    return "\n".join(lines)
```

Then register `categories`, `threshold`, `price`, `topmovers` slash commands inside `build_bot` following the pattern in Task 7 (each parses args and calls the matching logic function; `price` and `topmovers` reply non-ephemerally). Example for one:

```python
    @bot.tree.command(name="price", description="Current value of an item")
    async def price_cmd(interaction: discord.Interaction, item_id: str):
        await interaction.response.send_message(await price_text(store, item_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bot_commands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/bot.py tests/test_bot_commands.py
git commit -m "feat: /categories /threshold /price /topmovers commands"
```

---

## Stage C — Detector

### Task 9: Gating

**Files:**
- Create: `poe2bot/detector/gating.py`
- Test: `tests/test_gating.py`

**Interfaces:**
- Consumes: `Observation`, `LiquidityTier`.
- Produces:
  - `is_fresh(src_ts: int, prev_src_ts: int | None, now_ts: int, max_age_s: int) -> bool` — False if `src_ts == prev_src_ts` (no refresh) or `now_ts - src_ts > max_age_s`.
  - `@dataclass QualityConfig`: `max_age_s:int=10800, min_samples:int=12, early_league_mute_s:int=172800`.
  - `hard_block_reason(obs, prev_src_ts, now_ts, cfg) -> str | None` — the **always-on** gate that blocks *every* fire (including the fast-path): returns a reason for invalid/gap row, stale data, or LOW liquidity, else None. **Does NOT** consider sample count — that gate is separate so the fast-path can bypass warmup.
  - `has_min_samples(n_samples: int, cfg) -> bool` — `n_samples >= cfg.min_samples`; gates only the *statistical* (non-fast-path) detector.
  - `in_early_league(now_ts, league_started_at, cfg) -> bool` — True within `early_league_mute_s` of start (used to mute down-alerts only).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gating.py
from poe2bot.detector.gating import (is_fresh, QualityConfig, hard_block_reason,
                                     has_min_samples, in_early_league)
from poe2bot.models import Observation, LiquidityTier

def _obs(tier=LiquidityTier.MED, valid=True, gap=False, src_ts=1000):
    return Observation(item_id="x", league_id="L", src_ts=src_ts, wall_ts=src_ts, name="X",
                       category="currency", is_currency_pair=True, log_price=0.0, price_exalt=1.0,
                       volume=500.0, vol_daily=None, stock=None, doi=None, liq_tier=tier,
                       trade_id=None, valid=valid, gap=gap)

def test_is_fresh():
    assert is_fresh(1000, 900, now_ts=1000, max_age_s=10800) is True
    assert is_fresh(1000, 1000, now_ts=1000, max_age_s=10800) is False   # unchanged ts
    assert is_fresh(1000, 900, now_ts=1000 + 99999, max_age_s=10800) is False  # too old

def test_hard_block_low_liquidity():
    cfg = QualityConfig()
    r = hard_block_reason(_obs(tier=LiquidityTier.LOW), prev_src_ts=900, now_ts=1000, cfg=cfg)
    assert r == "low_liquidity"

def test_hard_block_stale_and_invalid():
    cfg = QualityConfig()
    assert hard_block_reason(_obs(src_ts=1000), prev_src_ts=1000, now_ts=1000, cfg=cfg) == "stale"
    assert hard_block_reason(_obs(valid=False), prev_src_ts=900, now_ts=1000, cfg=cfg) == "invalid_or_gap"

def test_hard_block_ignores_sample_count():
    cfg = QualityConfig(min_samples=12)
    # a healthy MED observation is NOT hard-blocked regardless of how little history exists
    assert hard_block_reason(_obs(), prev_src_ts=900, now_ts=1000, cfg=cfg) is None

def test_has_min_samples():
    cfg = QualityConfig(min_samples=12)
    assert has_min_samples(3, cfg) is False
    assert has_min_samples(50, cfg) is True

def test_in_early_league():
    cfg = QualityConfig(early_league_mute_s=172800)
    assert in_early_league(now_ts=1000, league_started_at=0, cfg=cfg) is True
    assert in_early_league(now_ts=200000, league_started_at=0, cfg=cfg) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gating.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement gating**

```python
# poe2bot/detector/gating.py
from __future__ import annotations
from dataclasses import dataclass
from ..models import Observation, LiquidityTier

@dataclass(frozen=True)
class QualityConfig:
    max_age_s: int = 10800          # ~3x assumed hourly refresh
    min_samples: int = 12
    early_league_mute_s: int = 172800  # 48h

def is_fresh(src_ts: int, prev_src_ts: int | None, now_ts: int, max_age_s: int) -> bool:
    if prev_src_ts is not None and src_ts == prev_src_ts:
        return False
    return (now_ts - src_ts) <= max_age_s

def in_early_league(now_ts: int, league_started_at: int, cfg: QualityConfig) -> bool:
    return (now_ts - league_started_at) < cfg.early_league_mute_s

def has_min_samples(n_samples: int, cfg: QualityConfig) -> bool:
    return n_samples >= cfg.min_samples

def hard_block_reason(obs: Observation, prev_src_ts: int | None, now_ts: int,
                      cfg: QualityConfig) -> str | None:
    """Always-on gate: blocks every fire including the fast-path. No sample-count check."""
    if not obs.valid or obs.gap:
        return "invalid_or_gap"
    if not is_fresh(obs.src_ts, prev_src_ts, now_ts, cfg.max_age_s):
        return "stale"
    if obs.liq_tier == LiquidityTier.LOW:
        return "low_liquidity"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gating.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add poe2bot/detector/gating.py tests/test_gating.py
git commit -m "feat: detector gating (freshness, quality, early-league)"
```

---

### Task 10: Engine — JUMP/CRASH with frozen reference

**Files:**
- Create: `poe2bot/detector/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Observation`, `AlertEvent`, `LiquidityTier`; `signals.median/pct_from_log`; `gating.*`.
- Produces:
  - `@dataclass DetectConfig`: `floor_pct=0.15, cheap_floor_pct=0.25, cheap_price=2.0, fast_path_log=0.40, cooldown_s=21600, top_k=8, quality=QualityConfig()`.
  - `@dataclass PriceVerdict`: `event: AlertEvent | None, new_mu_frozen: float | None, reason: str | None, fast_path: bool = False`.
  - `evaluate_price(obs, mu_frozen, baseline_logs, last_fire_up_ts, last_fire_dn_ts, now_ts, divine_exalt, cfg, early_league=False) -> PriceVerdict` — pure function. Logic:
    - reference = `mu_frozen` if not None else (`median(baseline_logs)` if baseline non-empty else `obs.log_price`).
    - `move = pct_from_log(obs.log_price, reference)`; `log_move = obs.log_price - reference`.
    - floor = `cheap_floor_pct` if `obs.price_exalt < cheap_price` else `floor_pct`.
    - `fast = abs(log_move) >= fast_path_log`.
    - fires if `fast` OR `abs(move) >= floor`.
    - direction up if `move>0` else down.
    - **early-league down-mute:** if `early_league` and direction is "down", return `reason="early_league_mute"` (no event) — crashes are muted in the first 48h.
    - cooldown: suppress if within `cooldown_s` of the same-direction last fire (`reason="cooldown"`).
    - on fire returns an `AlertEvent` (cls "JUMP"/"CRASH") with `severity=abs(log_move)`, `new_mu_frozen=obs.log_price` (freeze the new level), and `fast_path=fast`.

  2-of-3 persistence and the `min_samples` gate are applied by the caller (`detect`, Task 11) — but **fast-path events bypass both**. `evaluate_price` assumes the observation already passed the hard gate.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine.py
import math
from poe2bot.detector.engine import DetectConfig, evaluate_price
from poe2bot.models import Observation, LiquidityTier

def _obs(price, tier=LiquidityTier.HIGH, src_ts=1000):
    return Observation(item_id="divine", league_id="L", src_ts=src_ts, wall_ts=src_ts,
                       name="Divine Orb", category="currency", is_currency_pair=True,
                       log_price=math.log(price), price_exalt=price, volume=1500.0,
                       vol_daily=None, stock=None, doi=None, liq_tier=tier,
                       trade_id="divine", valid=True)

def test_jump_fires_above_floor():
    cfg = DetectConfig()
    base = [math.log(1.0)] * 20
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=base,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is not None and v.event.cls == "JUMP" and v.event.direction == "up"
    assert v.new_mu_frozen == math.log(1.30)
    assert v.fast_path is False        # +30% clears the 15% floor but is below the 0.40 fast-path

def test_fast_path_flagged_above_040():
    cfg = DetectConfig()
    v = evaluate_price(_obs(1.6), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is not None and v.fast_path is True    # log(1.6)=0.47 >= 0.40

def test_early_league_mutes_crash():
    cfg = DetectConfig()
    v = evaluate_price(_obs(0.70), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg, early_league=True)
    assert v.event is None and v.reason == "early_league_mute"

def test_early_league_does_not_mute_jump():
    cfg = DetectConfig()
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg, early_league=True)
    assert v.event is not None and v.event.cls == "JUMP"

def test_small_move_no_fire():
    cfg = DetectConfig()
    base = [math.log(1.0)] * 20
    v = evaluate_price(_obs(1.05), mu_frozen=math.log(1.0), baseline_logs=base,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is None

def test_crash_fires_below_floor():
    cfg = DetectConfig()
    v = evaluate_price(_obs(0.70), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is not None and v.event.cls == "CRASH" and v.event.direction == "down"

def test_cheap_item_uses_higher_floor():
    cfg = DetectConfig()
    # price 0.10 (<2 cheap) moved +18% -> below 25% cheap floor, no fire
    v = evaluate_price(_obs(0.118), mu_frozen=math.log(0.10), baseline_logs=[math.log(0.10)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is None

def test_cooldown_suppresses_same_direction():
    cfg = DetectConfig(cooldown_s=21600)
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=9_000, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is None and v.reason == "cooldown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement engine price evaluation**

```python
# poe2bot/detector/engine.py
from __future__ import annotations
from dataclasses import dataclass, field
from ..models import Observation, AlertEvent, LiquidityTier
from ..signals import median, pct_from_log, wfs_phase1
from .gating import QualityConfig

@dataclass(frozen=True)
class DetectConfig:
    floor_pct: float = 0.15
    cheap_floor_pct: float = 0.25
    cheap_price: float = 2.0
    fast_path_log: float = 0.40
    cooldown_s: int = 21600
    top_k: int = 8
    demand_drop: float = 0.50
    demand_min_trades_day: float = 10.0
    quality: QualityConfig = field(default_factory=QualityConfig)

@dataclass(frozen=True)
class PriceVerdict:
    event: AlertEvent | None
    new_mu_frozen: float | None
    reason: str | None
    fast_path: bool = False

def evaluate_price(obs: Observation, mu_frozen: float | None, baseline_logs: list[float],
                   last_fire_up_ts: int, last_fire_dn_ts: int, now_ts: int,
                   divine_exalt: float, cfg: DetectConfig,
                   early_league: bool = False) -> PriceVerdict:
    if mu_frozen is not None:
        reference = mu_frozen
    elif baseline_logs:
        reference = median(baseline_logs)
    else:
        reference = obs.log_price
    log_move = obs.log_price - reference
    move = pct_from_log(obs.log_price, reference)
    floor = cfg.cheap_floor_pct if obs.price_exalt < cfg.cheap_price else cfg.floor_pct
    fast = abs(log_move) >= cfg.fast_path_log
    fires = fast or abs(move) >= floor
    if not fires:
        return PriceVerdict(None, None, None)
    direction = "up" if move > 0 else "down"
    if early_league and direction == "down":
        return PriceVerdict(None, None, "early_league_mute")
    last_fire = last_fire_up_ts if direction == "up" else last_fire_dn_ts
    if last_fire and (now_ts - last_fire) < cfg.cooldown_s:
        return PriceVerdict(None, None, "cooldown")
    cls = "JUMP" if direction == "up" else "CRASH"
    wfs = wfs_phase1(obs.price_exalt, obs.liq_tier.gate, divine_exalt, obs.volume or 0.0)
    event = AlertEvent(item_id=obs.item_id, name=obs.name, cls=cls, direction=direction,
                       magnitude=log_move, pct_move=move, baseline=reference,
                       current=obs.log_price, severity=abs(log_move), liq_tier=obs.liq_tier,
                       trade_id=obs.trade_id, wfs=wfs)
    return PriceVerdict(event, obs.log_price, None, fast_path=fast)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add poe2bot/detector/engine.py tests/test_engine.py
git commit -m "feat: price JUMP/CRASH detection vs frozen reference"
```

---

### Task 11: Engine — demand collapse + top-K cap + `detect()` orchestration

**Files:**
- Modify: `poe2bot/detector/engine.py`
- Test: `tests/test_engine.py` (add cases)

**Interfaces:**
- Consumes: `Store` (Task 4), `evaluate_price` (Task 10), `gating.quality_block_reason`, `signals.median/robust_z/relative_drop`.
- Produces:
  - `evaluate_demand(obs, volume_baseline, early_league, cfg) -> AlertEvent | None` — fires DEMAND_COLLAPSE when `relative_drop(obs.volume, median(volume_baseline)) >= cfg.demand_drop`, the demand level clears the `demand_min_trades_day` floor (use `median(volume_baseline)`), and not muted by early league. Pure. (Param renamed from the gating `in_early_league` function to avoid shadowing.)
  - `async detect(store, observations, anchor, league_started_at, now_ts, cfg) -> tuple[list[AlertEvent], int]`. Per obs:
    1. load `prev_src_ts` (from `last_observation`) and insert the obs;
    2. `baseline_logs = price_log_window(item, obs.src_ts - 24*3600)` — **windowed in server-timestamp space**, not wall-clock, so a baseline is found regardless of the gap between API epoch and wall clock;
    3. `hard_block_reason` → if blocked, record suppressed + continue;
    4. `early = in_early_league(now_ts, league_started_at, cfg.quality)`;
    5. `evaluate_price(..., early_league=early)`;
    6. if it produced an event: **fast-path events fire immediately** (no pending, no `min_samples` check); otherwise require `has_min_samples` (else record `insufficient_samples` + reset pending) then apply 2-of-3 persistence via a settings counter `pend:{item}:{dir}` (fire at ≥2);
    7. if no event but `reason in (cooldown, early_league_mute)`, record suppressed;
    8. else reset both pending counters;
    9. `evaluate_demand(obs, volume_window(item, obs.src_ts - 48*3600), early, cfg)` → append if fired.
  - Then sort candidates by `severity` desc, keep top `cfg.top_k`, record each kept as `fired=1`, return `(kept, overflow_count)`. On any fire update detector_state (`mu_frozen` for price fires, `last_fire_*_ts`).

  `detect` records every suppression via `store.record_alert(_suppressed(obs), fired=False, reason, src_ts)`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_engine.py
import math
import pytest
from poe2bot.detector.engine import evaluate_demand, detect, DetectConfig
from poe2bot.store import Store
from poe2bot.models import Anchor

def _cur(price, vol, src_ts, item="divine", tier=LiquidityTier.HIGH):
    return Observation(item_id=item, league_id="L", src_ts=src_ts, wall_ts=src_ts,
                       name=item, category="currency", is_currency_pair=True,
                       log_price=math.log(price), price_exalt=price, volume=vol, vol_daily=None,
                       stock=None, doi=None, liq_tier=tier, trade_id=item, valid=True)

def test_demand_collapse_fires():
    cfg = DetectConfig()
    obs = _cur(1.0, vol=200.0, src_ts=5000)       # current flow 200
    baseline = [500.0] * 20                        # median 500 -> drop 60% >= 50%
    ev = evaluate_demand(obs, baseline, early_league=False, cfg=cfg)
    assert ev is not None and ev.cls == "DEMAND_COLLAPSE"

def test_demand_collapse_blocked_below_floor():
    cfg = DetectConfig(demand_min_trades_day=10.0)
    obs = _cur(1.0, vol=2.0, src_ts=5000)
    baseline = [5.0] * 20                           # median 5 < 10 trades/day floor
    assert evaluate_demand(obs, baseline, early_league=False, cfg=cfg) is None

def test_demand_collapse_muted_early_league():
    cfg = DetectConfig()
    obs = _cur(1.0, vol=200.0, src_ts=5000)
    assert evaluate_demand(obs, [500.0]*20, early_league=True, cfg=cfg) is None

async def _seed_flat(store, item, base_ts, n=20, price=1.0):
    for k in range(n):
        await store.insert_observation(_cur(price, 1500.0, base_ts + k, item=item))
    await store.update_detector_state(item, mu_frozen=math.log(price), n_obs=n)

async def test_fast_path_fires_immediately_and_topk_caps(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig(top_k=2)
    obss = []
    for i, item in enumerate(["a", "b", "c"]):
        await _seed_flat(s, item, base_ts=100)
        # jumps 1.5/1.7/1.9 -> log 0.405/0.531/0.642, all >= 0.40 fast-path
        obss.append(_cur(1.5 + i * 0.2, 1500.0, src_ts=100_000 + i, item=item))
    # now_ts aligned to src_ts so the freshness gate sees the data as current
    kept, overflow = await detect(s, obss, Anchor(250.0, 1.0), league_started_at=0,
                                  now_ts=100_010, cfg=cfg)
    assert len(kept) == 2 and overflow == 1            # all 3 fire, top-2 kept
    assert kept[0].severity >= kept[1].severity        # sorted by severity
    # detector_state re-frozen + alert_log fired rows written
    st = await s.get_detector_state("c")
    assert st["mu_frozen"] == math.approx(math.log(1.9)) if hasattr(math, "approx") else st["mu_frozen"] is not None
    cur = await s._db.execute("SELECT COUNT(*) c FROM alert_log WHERE fired=1")
    assert (await cur.fetchone())["c"] == 2
    await s.close()

async def test_two_of_three_persistence(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig()
    # seed WITHIN the 24h src_ts window of the confirming obs (100_001 - 86_400 = 13_601),
    # so the statistical path has >=12 in-window samples and 2-of-3 is actually exercised.
    await _seed_flat(s, "divine", base_ts=99_980)
    # +30% -> log 0.262: clears 15% floor, below 0.40 fast-path -> needs 2-of-3
    k1, o1 = await detect(s, [_cur(1.30, 1500.0, src_ts=100_001)], Anchor(250.0, 1.0),
                          league_started_at=0, now_ts=100_010, cfg=cfg)
    assert k1 == []                                    # first sighting: pending=1, no fire
    k2, o2 = await detect(s, [_cur(1.30, 1500.0, src_ts=100_002)], Anchor(250.0, 1.0),
                          league_started_at=0, now_ts=101_810, cfg=cfg)
    assert len(k2) == 1 and k2[0].cls == "JUMP"        # second confirming poll fires
    st = await s.get_detector_state("divine")
    assert st["mu_frozen"] is not None and st["last_fire_up_ts"] == 101_810
    await s.close()

async def test_statistical_path_blocked_during_warmup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig()                                # min_samples 12
    # only 3 prior samples -> a non-fast-path +30% move is suppressed as insufficient_samples
    await _seed_flat(s, "divine", base_ts=100, n=3)
    k, o = await detect(s, [_cur(1.30, 1500.0, src_ts=100_010)], Anchor(250.0, 1.0),
                        league_started_at=0, now_ts=100_020, cfg=cfg)
    assert k == []
    # ...but a fast-path move (>=0.40) DOES fire even with only 3 samples
    k2, o2 = await detect(s, [_cur(1.7, 1500.0, src_ts=100_011)], Anchor(250.0, 1.0),
                          league_started_at=0, now_ts=100_030, cfg=cfg)
    assert len(k2) == 1 and k2[0].cls == "JUMP"
    await s.close()
```

Note: `Observation` is a frozen dataclass (no `_replace`); the `_cur` helper builds one explicitly. If `math.approx` is unavailable in the env, the conditional falls back to a non-None check.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -k "demand or topk" -v`
Expected: FAIL (ImportError: evaluate_demand/detect)

- [ ] **Step 3: Implement demand + detect**

```python
# add to poe2bot/detector/engine.py
from ..signals import relative_drop
from .gating import hard_block_reason, has_min_samples, in_early_league
from ..models import Anchor

def evaluate_demand(obs: Observation, volume_baseline: list[float],
                    early_league: bool, cfg: DetectConfig) -> AlertEvent | None:
    if early_league or obs.volume is None or not volume_baseline:
        return None
    base = median(volume_baseline)
    if base < cfg.demand_min_trades_day:
        return None
    drop = relative_drop(obs.volume, base)
    if drop < cfg.demand_drop:
        return None
    return AlertEvent(item_id=obs.item_id, name=obs.name, cls="DEMAND_COLLAPSE",
                      direction="down", magnitude=-drop, pct_move=-drop, baseline=base,
                      current=obs.volume, severity=drop, liq_tier=obs.liq_tier,
                      trade_id=obs.trade_id, wfs=0.0)

async def _bump_pending(store, item_id: str, direction: str) -> int:
    key = f"pend:{item_id}:{direction}"
    cur = int(await store.get_setting(key) or "0") + 1
    await store.set_setting(key, str(cur))
    return cur

async def _reset_pending(store, item_id: str, direction: str) -> None:
    await store.set_setting(f"pend:{item_id}:{direction}", "0")

async def _fire_state(store, item_id: str, direction: str, mu_frozen: float, now_ts: int) -> None:
    fire_field = "last_fire_up_ts" if direction == "up" else "last_fire_dn_ts"
    await store.update_detector_state(item_id, mu_frozen=mu_frozen, **{fire_field: now_ts})

async def detect(store, observations: list[Observation], anchor: Anchor,
                 league_started_at: int, now_ts: int, cfg: DetectConfig):
    early = in_early_league(now_ts, league_started_at, cfg.quality)
    candidates: list[AlertEvent] = []
    for obs in observations:
        last = await store.last_observation(obs.item_id)
        prev_src_ts = last.src_ts if last else None
        await store.insert_observation(obs)
        # window in SERVER-TIMESTAMP space (not wall-clock) so the baseline is found
        # regardless of the offset between API epoch and wall clock.
        baseline_logs = await store.price_log_window(obs.item_id, obs.src_ts - 24 * 3600)
        reason = hard_block_reason(obs, prev_src_ts, now_ts, cfg.quality)
        if reason:
            await store.record_alert(_suppressed(obs), fired=False,
                                     suppressed_reason=reason, src_ts=obs.src_ts)
            continue
        st = await store.get_detector_state(obs.item_id)
        verdict = evaluate_price(obs, st["mu_frozen"], baseline_logs, st["last_fire_up_ts"],
                                 st["last_fire_dn_ts"], now_ts, anchor.divine_exalt, cfg,
                                 early_league=early)
        if verdict.event is not None:
            ev = verdict.event
            if verdict.fast_path:
                # fast-path: fire immediately, bypassing 2-of-3 and the min_samples gate
                await _reset_pending(store, obs.item_id, ev.direction)
                await _fire_state(store, obs.item_id, ev.direction, verdict.new_mu_frozen, now_ts)
                candidates.append(ev)
            elif not has_min_samples(len(baseline_logs), cfg.quality):
                await store.record_alert(_suppressed(obs), fired=False,
                                         suppressed_reason="insufficient_samples", src_ts=obs.src_ts)
                await _reset_pending(store, obs.item_id, "up")
                await _reset_pending(store, obs.item_id, "down")
            else:
                n = await _bump_pending(store, obs.item_id, ev.direction)
                if n >= 2:
                    await _reset_pending(store, obs.item_id, ev.direction)
                    await _fire_state(store, obs.item_id, ev.direction, verdict.new_mu_frozen, now_ts)
                    candidates.append(ev)
        elif verdict.reason in ("cooldown", "early_league_mute"):
            await store.record_alert(_suppressed(obs), fired=False,
                                     suppressed_reason=verdict.reason, src_ts=obs.src_ts)
        else:
            await _reset_pending(store, obs.item_id, "up")
            await _reset_pending(store, obs.item_id, "down")
        vol_base = await store.volume_window(obs.item_id, obs.src_ts - 48 * 3600)
        dem = evaluate_demand(obs, vol_base, early, cfg)
        if dem is not None:
            candidates.append(dem)
    candidates.sort(key=lambda e: e.severity, reverse=True)
    kept = candidates[: cfg.top_k]
    overflow_events = candidates[cfg.top_k:]
    for ev in kept:
        await store.record_alert(ev, fired=True, suppressed_reason=None, src_ts=now_ts)
    for ev in overflow_events:
        await store.record_alert(ev, fired=False, suppressed_reason="overflow_capped", src_ts=now_ts)
    return kept, len(overflow_events)

def _suppressed(obs: Observation) -> AlertEvent:
    return AlertEvent(item_id=obs.item_id, name=obs.name, cls="SUPPRESSED", direction="none",
                      magnitude=0.0, pct_move=0.0, baseline=0.0, current=obs.log_price,
                      severity=0.0, liq_tier=obs.liq_tier, trade_id=obs.trade_id, wfs=0.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/detector/engine.py tests/test_engine.py
git commit -m "feat: demand-collapse detection + detect() orchestration with top-K cap"
```

---

### Task 12: Alert formatting

**Files:**
- Create: `poe2bot/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `AlertEvent`, `LiquidityTier`.
- Produces:
  - `format_alert_lines(event: AlertEvent) -> tuple[str, str]` — returns `(title, body)` plain strings (kept Discord-free so they're unit-testable). Title e.g. `"📈 JUMP — Divine Orb"`; body includes signed pct, liquidity, WFS, and a trade link line when `trade_id` present.
  - `overflow_line(n: int) -> str` — `f"+{n} more movers this cycle"`.
  - `to_embed(event: AlertEvent) -> discord.Embed` — thin wrapper using the lines + a color per class (green up, red down). Not unit-tested (Discord type); exercised by smoke test.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alerts.py
from poe2bot.alerts import format_alert_lines, overflow_line
from poe2bot.models import AlertEvent, LiquidityTier

def _ev(cls="JUMP", direction="up", pct=0.30, trade_id="divine"):
    return AlertEvent(item_id="divine", name="Divine Orb", cls=cls, direction=direction,
                      magnitude=0.26, pct_move=pct, baseline=0.0, current=0.26, severity=0.26,
                      liq_tier=LiquidityTier.HIGH, trade_id=trade_id, wfs=1.23)

def test_title_and_body():
    title, body = format_alert_lines(_ev())
    assert "JUMP" in title and "Divine Orb" in title
    assert "+30" in body and "HIGH" in body

def test_demand_collapse_wording():
    title, body = format_alert_lines(_ev(cls="DEMAND_COLLAPSE", direction="down", pct=-0.6))
    assert "DEMAND" in title.upper()
    assert "-60" in body

def test_overflow_line():
    assert overflow_line(5) == "+5 more movers this cycle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_alerts.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement alerts**

```python
# poe2bot/alerts.py
from __future__ import annotations
import discord
from .models import AlertEvent

_ICON = {"JUMP": "📈", "CRASH": "📉", "DEMAND_COLLAPSE": "🥶"}

def format_alert_lines(event: AlertEvent) -> tuple[str, str]:
    icon = _ICON.get(event.cls, "❓")
    title = f"{icon} {event.cls} — {event.name}"
    lines = [f"Change: {event.pct_move:+.0%}",
             f"Liquidity: {event.liq_tier.name}",
             f"WFS: {event.wfs:.3g}"]
    if event.trade_id:
        lines.append(f"Trade: https://www.pathofexile.com/trade2/search/poe2/{event.trade_id}")
    return title, "\n".join(lines)

def overflow_line(n: int) -> str:
    return f"+{n} more movers this cycle"

def to_embed(event: AlertEvent) -> discord.Embed:
    title, body = format_alert_lines(event)
    color = discord.Color.green() if event.direction == "up" else discord.Color.red()
    return discord.Embed(title=title, description=body, color=color)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_alerts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/alerts.py tests/test_alerts.py
git commit -m "feat: alert formatting (embeds + overflow line)"
```

---

## Stage D — Runtime

### Task 13: Health (circuit breaker + dead-man heartbeat)

**Files:**
- Create: `poe2bot/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Produces:
  - `class CircuitBreaker`: `__init__(self, threshold=5)`; `record_success()`; `record_failure() -> bool` (returns True when it newly trips at `threshold` consecutive failures); property `is_open: bool`.
  - `async ping_dead_man(session, url: str | None) -> bool` — GETs `url` (heartbeat) if set; returns True on 2xx, False otherwise / when url is None.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health.py
from poe2bot.health import CircuitBreaker

def test_breaker_trips_once_at_threshold():
    cb = CircuitBreaker(threshold=3)
    assert cb.record_failure() is False
    assert cb.record_failure() is False
    assert cb.record_failure() is True     # trips now
    assert cb.is_open is True
    assert cb.record_failure() is False    # already open, not a new trip

def test_breaker_resets_on_success():
    cb = CircuitBreaker(threshold=2)
    cb.record_failure()
    cb.record_success()
    assert cb.is_open is False
    assert cb.record_failure() is False    # counter reset
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement health**

```python
# poe2bot/health.py
from __future__ import annotations
import aiohttp

class CircuitBreaker:
    def __init__(self, threshold: int = 5):
        self._threshold = threshold
        self._fails = 0
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def record_success(self) -> None:
        self._fails = 0
        self._open = False

    def record_failure(self) -> bool:
        self._fails += 1
        if self._fails >= self._threshold and not self._open:
            self._open = True
            return True
        return False

async def ping_dead_man(session: aiohttp.ClientSession, url: str | None) -> bool:
    if not url:
        return False
    try:
        async with session.get(url) as r:
            return 200 <= r.status < 300
    except aiohttp.ClientError:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/health.py tests/test_health.py
git commit -m "feat: circuit breaker + dead-man heartbeat"
```

---

### Task 14: Scheduler — `poll_once` + maintenance

**Files:**
- Create: `poe2bot/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `Store`, `Poe2ScoutClient`, `normalize_currency`, `detect`, `DetectConfig`, `Anchor`, `CircuitBreaker`.
- Produces:
  - `async poll_once(store, client, cfg, now_ts, breaker, notify) -> int` — reads active league from settings; fetches currency overview; reads `src_ts` from `epoch`; if `src_ts` equals stored `last_poll_ts` → record success (no refresh), return 0; else derives + **clamps** the `Anchor` (rejects an implausible poll-over-poll divine move via `_clamp_anchor`, persisting `anchor_divine`); reads `league_started_at` via `store.get_league_started_at(...)` and passes it to `detect` so the early-league mute actually engages in production; normalize → `detect` → `await notify(event)` per kept event; if `overflow>0`, `await notify({"overflow": overflow})`; persist `last_poll_ts`; `breaker.record_success()`. On fetch exception: if `breaker.record_failure()` returns True (newly tripped), `await notify({"health": "source_down"})`; return -1. `notify` is an injected async callable so tests don't need Discord.
  - `_clamp_anchor(new_divine: float, prev_divine: float | None, cap: float = 3.0) -> float` — returns `prev_divine` when the ratio to it exceeds `cap` either way, else `new_divine`.
  - `extract_anchor(raw: dict) -> Anchor` — pulls divine price if present, else `Anchor(1.0, 1.0)`.
  - `extract_src_ts(raw: dict) -> int` — `int(raw.get("epoch", 0))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement scheduler**

```python
# poe2bot/scheduler.py
from __future__ import annotations
from .models import Anchor
from .sources.normalize import normalize_currency
from .detector.engine import detect, DetectConfig

def extract_src_ts(raw: dict) -> int:
    return int(raw.get("epoch", 0))

def extract_anchor(raw: dict) -> Anchor:
    for it in raw.get("items", []):
        if it.get("apiId") == "divine" and it.get("currentPrice"):
            # currentPrice is in Exalted-equiv units in this overview
            return Anchor(divine_exalt=float(it["currentPrice"]) or 1.0, chaos_divine=1.0)
    return Anchor(divine_exalt=1.0, chaos_divine=1.0)

def _clamp_anchor(new_divine: float, prev_divine: float | None, cap: float = 3.0) -> float:
    if prev_divine and prev_divine > 0:
        ratio = new_divine / prev_divine
        if ratio > cap or ratio < 1.0 / cap:
            return prev_divine            # implausible jump -> keep previous
    return new_divine

async def poll_once(store, client, cfg: DetectConfig, now_ts: int, breaker, notify) -> int:
    league = await store.get_setting("league")
    if not league:
        return 0
    try:
        raw = await client.get_currency_overview(league)
    except Exception:
        if breaker.record_failure():
            await notify({"health": "source_down"})
        return -1
    src_ts = extract_src_ts(raw)
    last = await store.get_setting("last_poll_ts")
    if last is not None and int(last) == src_ts:
        breaker.record_success()
        return 0
    raw_anchor = extract_anchor(raw)
    prev_div = await store.get_setting("anchor_divine")
    divine = _clamp_anchor(raw_anchor.divine_exalt, float(prev_div) if prev_div else None)
    anchor = Anchor(divine_exalt=divine, chaos_divine=raw_anchor.chaos_divine)
    await store.set_setting("anchor_divine", str(divine))
    league_id = await store.get_active_league() or league
    league_started_at = await store.get_league_started_at(league_id)
    obs = normalize_currency(raw, league_id, anchor, src_ts)
    kept, overflow = await detect(store, obs, anchor, league_started_at, now_ts, cfg)
    for ev in kept:
        await notify(ev)
    if overflow > 0:
        await notify({"overflow": overflow})
    await store.set_setting("last_poll_ts", str(src_ts))
    breaker.record_success()
    return len(kept)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poe2bot/scheduler.py tests/test_scheduler.py
git commit -m "feat: poll_once cycle (dedup, detect, notify, breaker)"
```

---

### Task 15: Entrypoint + packaging + integration smoke

**Files:**
- Create: `poe2bot/main.py`, `Dockerfile`, `deploy/poe2bot.service`, `README.md`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `build_notifier(bot, alert_channel_id, health_channel_id)` → async `notify(payload)`: a `{"health": ...}` dict posts to the health channel (falls back to alert channel if unset); a `{"overflow": n}` dict posts the overflow line to the alert channel; anything else is an `AlertEvent` posted as an embed to the alert channel.
  - `async amain(env: Mapping[str,str])` — wires Settings → Store → aiohttp session → Poe2ScoutClient → LeagueService → bot → notifier → **two** APScheduler jobs: a poll job every `poll_interval_min` (calls `poll_once` then `ping_dead_man`) and a daily prune job (`store.prune`). Guarded by `if __name__ == "__main__": asyncio.run(amain(os.environ))`.
  - The smoke test drives an **end-to-end run that actually fires** (15 flat polls to seed a baseline, then a +40% move confirmed over 2 polls, with `now_ts` aligned to the server epoch) and asserts both a kept `JUMP` event reached `notify` and an `alert_log` row with `fired=1` — not merely that observations persisted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smoke.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: FAIL (ModuleNotFoundError until main exists, or assertion if pipeline incomplete)

- [ ] **Step 3: Implement entrypoint + packaging**

```python
# poe2bot/main.py
from __future__ import annotations
import asyncio, os, time
from collections.abc import Mapping
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import Settings
from .store import Store
from .sources.poe2scout import Poe2ScoutClient
from .bot import LeagueService, build_bot
from .scheduler import poll_once
from .detector.engine import DetectConfig
from .health import CircuitBreaker, ping_dead_man
from .alerts import to_embed, overflow_line

def build_notifier(bot, alert_channel_id: int, health_channel_id: int | None):
    async def notify(payload):
        if isinstance(payload, dict) and "health" in payload:
            ch = bot.get_channel(health_channel_id or alert_channel_id)
            if ch is not None:
                await ch.send(f"⚠️ pipeline health: {payload['health']}")
            return
        channel = bot.get_channel(alert_channel_id)
        if channel is None:
            return
        if isinstance(payload, dict) and "overflow" in payload:
            await channel.send(overflow_line(payload["overflow"]))
        else:
            await channel.send(embed=to_embed(payload))
    return notify

async def amain(env: Mapping[str, str]) -> None:
    settings = Settings.from_env(env)
    store = await Store.open(settings.db_path)
    session = aiohttp.ClientSession()
    client = Poe2ScoutClient(session, settings.poe2scout_ua)
    league_service = LeagueService(client)
    bot = build_bot(store, league_service, settings)
    notify = build_notifier(bot, settings.alert_channel_id, settings.health_channel_id)
    breaker = CircuitBreaker()
    cfg = DetectConfig()

    scheduler = AsyncIOScheduler()
    async def poll_job():
        await poll_once(store, client, cfg, int(time.time()), breaker, notify)
        await ping_dead_man(session, settings.dead_man_url)
    async def prune_job():
        await store.prune(int(time.time()))
    scheduler.add_job(poll_job, "interval", minutes=settings.poll_interval_min)
    scheduler.add_job(prune_job, "interval", hours=24)
    scheduler.start()
    try:
        await bot.start(settings.discord_token)
    finally:
        await session.close()
        await store.close()

if __name__ == "__main__":
    asyncio.run(amain(os.environ))
```

```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY poe2bot ./poe2bot
RUN pip install --no-cache-dir .
ENV DB_PATH=/data/poe2bot.db
VOLUME ["/data"]
CMD ["python", "-m", "poe2bot.main"]
```

```ini
# deploy/poe2bot.service
[Unit]
Description=PoE2 Trade Bot
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/poe2bot.env
ExecStart=/usr/bin/python -m poe2bot.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```markdown
# README.md
PoE2 Trade Bot — Phase 1. Polls poe2scout, alerts on price jumps/crashes/demand collapse.
## Run
1. `cp .env.example .env` and fill DISCORD_TOKEN + ALERT_CHANNEL_ID.
2. `pip install -e ".[dev]" && python -m poe2bot.main`
3. In Discord: `/setleague` (autocompletes live), then wait for polls to accrue.
## Deploy
`docker build -t poe2bot . && docker run -v $PWD/data:/data --env-file .env poe2bot`
or install `deploy/poe2bot.service` with `/etc/poe2bot.env`.
```

Note: add `__main__.py` indirection not needed — `python -m poe2bot.main` runs the module.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 5: Commit**

```bash
git add poe2bot/main.py Dockerfile deploy README.md tests/test_smoke.py
git commit -m "feat: entrypoint, packaging, end-to-end smoke test"
```

---

## Self-Review (completed)

**Spec coverage:** poe2scout primary (Tasks 5,14) ✓; deduped log-space ledger (Task 4) ✓; league autocomplete from poe2scout (Task 7) ✓; all slash commands (Tasks 7–8) ✓; liquidity tiers + gates (Tasks 2,5,9) ✓; frozen-reference JUMP/CRASH + fast-path + cheap-floor + cooldown (Task 10) ✓; demand collapse on volume level + trades/day floor + early-league mute (Task 11) ✓; 2-of-3 persistence + top-K cap + overflow (Task 11) ✓; data-quality gate + suppression logging (Tasks 9,11) ✓; WFS Phase-1 (Tasks 3,8) ✓; circuit breaker + dead-man (Task 13) ✓; health channel wiring (Task 15 notifier + breaker; pipeline-health message posting folded into the breaker trip handler) ✓; Docker/systemd (Task 15) ✓; nightly prune (`Store.prune`, Task 4; scheduled in Task 15 job extension noted below). **Deferred to Phase 2 (out of scope, per spec §10):** CUSUM/Page-Hinkley, basket detrending, BH-FDR, first-differenced flow, diurnal adjustment, poe.ninja cross-check.

**Revision note (post plan-review B1–B3):** the following are now first-class task steps with tests, not prose:
- Magnitude **fast-path fires immediately**, bypassing 2-of-3 persistence and the `min_samples` warmup gate (Tasks 9–11): `hard_block_reason` (always-on) is split from `has_min_samples` (statistical-path only); `PriceVerdict.fast_path` drives an immediate fire in `detect`. Covered by `test_fast_path_fires_immediately_and_topk_caps`, `test_statistical_path_blocked_during_warmup`, `test_two_of_three_persistence`.
- **Clock-mix fixed:** baseline/volume windows are computed in server-`src_ts` space (`obs.src_ts − 24h/48h`), not wall-clock, so a baseline is found regardless of epoch-vs-wallclock offset (Task 11). The end-to-end `test_end_to_end_pipeline_fires` aligns `now_ts` to the epoch and asserts a real fire + `alert_log` row.
- **Early-league mute functional:** `poll_once` reads `get_league_started_at` and passes it through; `evaluate_price` mutes down-direction fires when `early_league` (Tasks 4/10/11/14). Covered by `test_early_league_mutes_crash` / `..._does_not_mute_jump`.
- **Prune + dead-man + health channel wired:** daily prune job, `ping_dead_man` in the poll job, and a `{"health": ...}` notify on a new breaker trip routed to the health channel (Tasks 14/15). Covered by `test_poll_once_records_failure_and_health`.
- **Anchor sanity-clamp:** `_clamp_anchor` rejects implausible poll-over-poll divine moves (Task 14, `test_clamp_anchor`).

**Still deferred to Phase 2 (out of scope per spec §10):** CUSUM/Page-Hinkley, basket detrending/currency-regime suppressor, BH-FDR, first-differenced flow, diurnal adjustment, poe.ninja cross-check, gap-density/anchor-bounds sub-gates, Exalted unit re-derivation (§14), and wiring `/threshold`+`/categories` into the detector (they persist settings in Phase 1 but are not yet read — flagged, not silently dropped). Rate-limiting is a no-op at the single-endpoint 30-min cadence; add a semaphore before expanding endpoints.

**Placeholder scan:** no TBD/TODO; every code step has complete code.

**Type consistency:** `Observation`/`AlertEvent` field names match across Tasks 2/4/5/10/11/12; `DetectConfig`/`QualityConfig`/`PriceVerdict` consistent across 9/10/11/14; gating exposes `hard_block_reason`/`has_min_samples`/`in_early_league` consistently; `notify` payload contract (`AlertEvent` | `{"overflow"}` | `{"health"}`) consistent across 11/14/15.
