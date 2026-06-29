# Currency-Display Breakpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the digest tables' currency columns from Ex/Chaos to Chaos/Div once 1 chaos is worth ≥ a configurable breakpoint (default 20) exalts.

**Architecture:** A new neutral `poe2bot/display.py` owns the mode decision (`exalt_per_chaos = divine_exalt / chaos_divine`; `>= breakpoint` → chaos-mode) and the per-mode column map. The scheduler resolves mode at the fresh anchor and stamps it into each digest payload; notifiers thread it to the digest formatters; `/status` shows it; a new `/pricebreakpoint` command sets the threshold. `AlertEvent` already carries all three prices, so formatters only *select* two by mode — no currency recompute, no DB migration.

**Tech Stack:** Python 3, discord.py 2.x, aiosqlite, APScheduler, pytest + pytest-asyncio (`asyncio_mode = "auto"`).

## Global Constraints

- Breakpoint compares **Exalted-per-Chaos = `divine_exalt / chaos_divine`**; `>= breakpoint` flips to chaos-mode, `<` stays exalt-mode. Default breakpoint **20.0**.
- Mode strings are exactly `"exalt"` and `"chaos"`.
- `MODE_COLS["exalt"] == (("Ex","price_exalt"),("Chaos","price_chaos"))`; `MODE_COLS["chaos"] == (("Chaos","price_chaos"),("Div","price_div"))`.
- All digest formatter `mode` params **default to `"exalt"`**, so exalt-mode output is byte-identical to today and untouched callers/tests keep passing.
- Only the digest path changes. `/price` (`price_text`), `format_price`, the legacy `to_embed` path, and the `--once` summary keep their three-currency `ex | div | chaos` line.
- Breakpoint setting key is `display_chaos_breakpoint`. No schema change (`settings` is generic key/value).
- Divide-by-zero guard: `exalt_per_chaos` uses `max(chaos_divine, eps)` with `eps = 1e-9`.
- New write command `/pricebreakpoint` is gated with `@app_commands.default_permissions(manage_guild=True)`.
- Commits authored `ThatOneGuy2259 <60535876+ThatOneGuy2259@users.noreply.github.com>`, co-authored `Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Never reintroduce the work email. No secrets in code.

---

### Task 1: `poe2bot/display.py` — mode logic, column map, resolvers

**Files:**
- Create: `poe2bot/display.py`
- Test: `tests/test_display.py`

**Interfaces:**
- Consumes: `store.get_setting` (existing).
- Produces:
  - `DEFAULT_CHAOS_EXALT_BREAKPOINT: float = 20.0`
  - `BREAKPOINT_SETTING: str = "display_chaos_breakpoint"`
  - `MODE_COLS: dict[str, tuple[tuple[str,str], tuple[str,str]]]`
  - `exalt_per_chaos(divine_exalt: float, chaos_divine: float, eps: float = 1e-9) -> float`
  - `display_mode(divine_exalt: float, chaos_divine: float, breakpoint: float) -> str`
  - `async resolve_breakpoint(store) -> float`
  - `async resolve_mode(store, divine_exalt: float, chaos_divine: float) -> str`

- [ ] **Step 1: Write the failing test** — `tests/test_display.py`

```python
import pytest
from poe2bot.store import Store
from poe2bot.display import (
    DEFAULT_CHAOS_EXALT_BREAKPOINT, BREAKPOINT_SETTING, MODE_COLS,
    exalt_per_chaos, display_mode, resolve_breakpoint, resolve_mode)


def test_exalt_per_chaos_ratio():
    # 200 ex/div divided by 10 chaos/div -> 20 ex per chaos
    assert exalt_per_chaos(200.0, 10.0) == pytest.approx(20.0)


def test_exalt_per_chaos_zero_chaos_guarded():
    assert exalt_per_chaos(200.0, 0.0) > 0          # eps guard, no ZeroDivisionError


def test_display_mode_below_breakpoint_is_exalt():
    assert display_mode(190.0, 10.0, 20.0) == "exalt"     # 19 ex/chaos < 20


def test_display_mode_at_breakpoint_is_chaos():
    assert display_mode(200.0, 10.0, 20.0) == "chaos"     # exactly 20 -> chaos


def test_display_mode_above_breakpoint_is_chaos():
    assert display_mode(240.0, 10.0, 20.0) == "chaos"     # 24 ex/chaos


def test_mode_cols_exact_shape():
    assert MODE_COLS["exalt"] == (("Ex", "price_exalt"), ("Chaos", "price_chaos"))
    assert MODE_COLS["chaos"] == (("Chaos", "price_chaos"), ("Div", "price_div"))


async def test_resolve_breakpoint_default(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert await resolve_breakpoint(s) == DEFAULT_CHAOS_EXALT_BREAKPOINT
    await s.close()


async def test_resolve_breakpoint_override(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting(BREAKPOINT_SETTING, "35")
    assert await resolve_breakpoint(s) == 35.0
    await s.close()


async def test_resolve_mode_end_to_end(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    assert await resolve_mode(s, 190.0, 10.0) == "exalt"          # 19 < default 20
    await s.set_setting(BREAKPOINT_SETTING, "15")
    assert await resolve_mode(s, 190.0, 10.0) == "chaos"          # 19 >= 15 now
    await s.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_display.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'poe2bot.display'`.

- [ ] **Step 3: Write the minimal implementation** — `poe2bot/display.py`

```python
from __future__ import annotations

DEFAULT_CHAOS_EXALT_BREAKPOINT = 20.0   # 1 chaos worth >= this many exalts -> chaos-mode
BREAKPOINT_SETTING = "display_chaos_breakpoint"

# (header, AlertEvent attribute) for (primary, secondary), per mode. AlertEvent already
# carries all three prices, so formatters select two of them by mode — never recompute.
MODE_COLS: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "exalt": (("Ex", "price_exalt"), ("Chaos", "price_chaos")),
    "chaos": (("Chaos", "price_chaos"), ("Div", "price_div")),
}


def exalt_per_chaos(divine_exalt: float, chaos_divine: float, eps: float = 1e-9) -> float:
    """Exalted per Chaos. Chaos-per-Exalt = chaos_divine / divine_exalt, so its inverse is
    divine_exalt / chaos_divine. eps guards a 0 chaos_divine from dividing by zero."""
    return divine_exalt / max(chaos_divine, eps)


def display_mode(divine_exalt: float, chaos_divine: float, breakpoint: float) -> str:
    """'chaos' once 1 chaos is worth >= breakpoint exalts, else 'exalt'."""
    return "chaos" if exalt_per_chaos(divine_exalt, chaos_divine) >= breakpoint else "exalt"


async def resolve_breakpoint(store) -> float:
    v = await store.get_setting(BREAKPOINT_SETTING)
    return float(v) if v else DEFAULT_CHAOS_EXALT_BREAKPOINT


async def resolve_mode(store, divine_exalt: float, chaos_divine: float) -> str:
    return display_mode(divine_exalt, chaos_divine, await resolve_breakpoint(store))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_display.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add poe2bot/display.py tests/test_display.py
git commit -m "feat: currency-display mode logic and column map"
```

---

### Task 2: `alerts.py` digest formatters honor `mode`

**Files:**
- Modify: `poe2bot/alerts.py` (`_digest_row`, `format_digest`, `to_digest_embed`; remove the module-level `_DIGEST_HEADERS` constant — headers are now mode-derived)
- Test: `tests/test_alerts.py` (add)

**Interfaces:**
- Consumes: `display.MODE_COLS`.
- Produces:
  - `_digest_row(e: AlertEvent, mode: str = "exalt") -> tuple[str, str, str, str]`
  - `format_digest(events: list[AlertEvent], mode: str = "exalt", max_rows: int = 40) -> str`
  - `to_digest_embed(events: list[AlertEvent], kind: str, mode: str = "exalt") -> discord.Embed`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_alerts.py`

```python
from poe2bot.models import AlertEvent, LiquidityTier
from poe2bot.alerts import format_digest, to_digest_embed, _digest_row


def _ev(name="Mirror", ex=100.0, div=0.5, chaos=50.0, pct=0.3, cls="JUMP", direction="up"):
    return AlertEvent(item_id="i", name=name, cls=cls, direction=direction,
                      magnitude=0.26, pct_move=pct, baseline=1.0, current=1.3, severity=1.0,
                      liq_tier=LiquidityTier.HIGH, trade_id=None, wfs=1.0,
                      price_exalt=ex, price_div=div, price_chaos=chaos, low_confidence=False)


def test_digest_exalt_mode_headers_and_cells():
    out = format_digest([_ev()], mode="exalt")
    header, row = out.splitlines()[0], out.splitlines()[1]
    assert "Ex" in header and "Chaos" in header and "Div" not in header
    assert "100" in row and "50" in row          # price_exalt primary, price_chaos secondary


def test_digest_chaos_mode_headers_and_cells():
    out = format_digest([_ev()], mode="chaos")
    header, row = out.splitlines()[0], out.splitlines()[1]
    assert "Chaos" in header and "Div" in header and "Ex" not in header
    assert "50" in row and "0.5" in row          # price_chaos primary, price_div secondary


def test_digest_default_mode_is_exalt():
    assert format_digest([_ev()]) == format_digest([_ev()], mode="exalt")


def test_digest_row_selects_attributes_by_mode():
    e = _ev(ex=100.0, chaos=50.0, div=0.5)
    ex_row, ch_row = _digest_row(e, "exalt"), _digest_row(e, "chaos")
    assert ex_row[1] == "100" and ex_row[2] == "50"     # ex, chaos
    assert ch_row[1] == "50" and ch_row[2] == "0.5"     # chaos, div


def test_to_digest_embed_threads_mode():
    assert "Div" in to_digest_embed([_ev()], "jumps", mode="chaos").description
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_alerts.py -q`
Expected: FAIL — `_digest_row()`/`format_digest()` reject the `mode` argument (TypeError) or chaos-mode assertions fail.

- [ ] **Step 3: Edit `poe2bot/alerts.py`**

Add the import near the top (after `from .models import AlertEvent`):

```python
from .display import MODE_COLS
```

Delete the `_DIGEST_HEADERS = ("Item", "Ex", "Chaos", "Info")` line. Replace `_digest_row`, `format_digest`, and `to_digest_embed` with:

```python
def _digest_row(e: AlertEvent, mode: str = "exalt") -> tuple[str, str, str, str]:
    (_, p_attr), (_, s_attr) = MODE_COLS[mode]
    info = f"{e.pct_move:+.0%}"
    if e.cls == "DEMAND_COLLAPSE":
        info += " vol"                       # the move is a volume drop, not a price move
    if e.low_confidence:
        info += " ⚠"                         # thin market — price may be unreliable
    return (e.name[:22], humanize(getattr(e, p_attr)), humanize(getattr(e, s_attr)), info)


def format_digest(events: list[AlertEvent], mode: str = "exalt", max_rows: int = 40) -> str:
    """A monospace table (Item · primary · secondary · Info) of one direction's movers.
    Columns are chosen by `mode`: exalt-mode = Ex/Chaos, chaos-mode = Chaos/Div."""
    (p_hdr, _), (s_hdr, _) = MODE_COLS[mode]
    headers = ("Item", p_hdr, s_hdr, "Info")
    rows = [_digest_row(e, mode) for e in events[:max_rows]]
    cols = list(zip(*([headers] + rows)))                  # column-wise for width sizing
    w = [max(len(str(c)) for c in col) for col in cols]
    def fmt(r): return f"{r[0]:<{w[0]}}  {r[1]:>{w[1]}}  {r[2]:>{w[2]}}  {r[3]:<{w[3]}}"
    lines = [fmt(headers)] + [fmt(r) for r in rows]
    if len(events) > max_rows:
        lines.append(f"…and {len(events) - max_rows} more")
    return "\n".join(lines)


def to_digest_embed(events: list[AlertEvent], kind: str, mode: str = "exalt") -> discord.Embed:
    """One embed per direction: kind='jumps' (green 📈) or 'drops' (red 📉)."""
    icon = "📈" if kind == "jumps" else "📉"
    color = discord.Color.green() if kind == "jumps" else discord.Color.red()
    title = f"{icon} {kind.capitalize()} ({len(events)})"
    return discord.Embed(title=title,
                         description="```\n" + format_digest(events, mode) + "\n```",
                         color=color)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_alerts.py -q`
Expected: PASS (existing tests + 5 new).

- [ ] **Step 5: Commit**

```bash
git add poe2bot/alerts.py tests/test_alerts.py
git commit -m "feat: digest formatters select currency columns by mode"
```

---

### Task 3: Stamp `mode` through scheduler + notifiers

**Files:**
- Modify: `poe2bot/scheduler.py` (compute mode after anchor, add to both digest payloads)
- Modify: `poe2bot/main.py` (`build_notifier.notify` and `_stdout_notify` thread `payload.get("mode", "exalt")`)
- Test: `tests/test_scheduler.py` (add)

**Interfaces:**
- Consumes: `display.resolve_mode`; `alerts.to_digest_embed(events, kind, mode)`, `alerts.format_digest(events, mode)`.
- Produces: digest payloads shaped `{"digest": [...], "kind": "jumps"|"drops", "mode": "exalt"|"chaos"}`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_scheduler.py`

```python
import poe2bot.scheduler as sched
from poe2bot.store import Store
from poe2bot.detector.engine import DetectConfig
from poe2bot.health import CircuitBreaker
from poe2bot.models import AlertEvent, LiquidityTier


def _ev(direction):
    return AlertEvent(item_id="i", name="Mirror", cls="JUMP" if direction == "up" else "CRASH",
                      direction=direction, magnitude=0.26, pct_move=0.3, baseline=1.0,
                      current=1.3, severity=1.0, liq_tier=LiquidityTier.HIGH, trade_id=None,
                      wfs=1.0, price_exalt=100.0, price_div=0.5, price_chaos=50.0,
                      low_confidence=False)


class _MetaClient:
    """Empty currency payload (detect is monkeypatched) + league meta fixing the ex/chaos ratio."""
    def __init__(self, divine, chaos):
        self.divine, self.chaos = divine, chaos

    async def get_league_meta(self, league):
        return {"DivinePrice": self.divine, "ChaosDivinePrice": self.chaos}

    async def get_currency_overview(self, league, category="currency"):
        return {"CurrentPage": 1, "Pages": 1, "Total": 0, "Items": []}


async def _run_mode(tmp_path, monkeypatch, divine, chaos):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    async def fake_detect(*a, **k): return ([_ev("up"), _ev("down")], 0)
    monkeypatch.setattr(sched, "detect", fake_detect)
    sent = []
    async def notify(p): sent.append(p)
    await sched.poll_once(s, _MetaClient(divine, chaos), DetectConfig(),
                          now_ts=1000, breaker=CircuitBreaker(), notify=notify)
    await s.close()
    return [p for p in sent if isinstance(p, dict) and "digest" in p]


async def test_digest_payloads_carry_chaos_mode(tmp_path, monkeypatch):
    digests = await _run_mode(tmp_path, monkeypatch, divine=240.0, chaos=10.0)   # 24 ex/chaos
    assert digests and all(p["mode"] == "chaos" for p in digests)
    assert {p["kind"] for p in digests} == {"jumps", "drops"}


async def test_digest_payloads_carry_exalt_mode(tmp_path, monkeypatch):
    digests = await _run_mode(tmp_path, monkeypatch, divine=190.0, chaos=10.0)   # 19 ex/chaos
    assert digests and all(p["mode"] == "exalt" for p in digests)
```

Also append a stdout-notifier tolerance test to `tests/test_alerts.py` (it has the `_ev` factory from Task 2):

```python
async def test_stdout_notify_handles_mode_key(capsys):
    import poe2bot.main as mainmod
    await mainmod._stdout_notify({"digest": [_ev()], "kind": "jumps", "mode": "chaos"})
    out = capsys.readouterr().out
    assert "JUMPS" in out and "Div" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_scheduler.py tests/test_alerts.py::test_stdout_notify_handles_mode_key -q`
Expected: FAIL — payloads lack `"mode"` (KeyError on `p["mode"]`).

- [ ] **Step 3a: Edit `poe2bot/scheduler.py`**

Add the import beside the others:

```python
from .display import resolve_mode
```

In `poll_once`, replace the digest-building block:

```python
    kept, overflow = await detect(store, obs, anchor, started, now_ts, cfg, category_floors)
    # Choose display units once, at the fresh anchor, and stamp it into each payload so every
    # consumer renders the same currency columns.
    mode = await resolve_mode(store, anchor.divine_exalt, anchor.chaos_divine)
    ups = [ev for ev in kept if ev.direction == "up"]
    downs = [ev for ev in kept if ev.direction == "down"]
    if ups:
        await notify({"digest": ups, "kind": "jumps", "mode": mode})
    if downs:
        await notify({"digest": downs, "kind": "drops", "mode": mode})
    if overflow > 0:
        await notify({"overflow": overflow})
```

- [ ] **Step 3b: Edit `poe2bot/main.py`**

In `build_notifier.notify`, the digest branch:

```python
        if isinstance(payload, dict) and "digest" in payload:
            await channel.send(embed=to_digest_embed(
                payload["digest"], payload["kind"], payload.get("mode", "exalt")))
```

In `_stdout_notify`, the digest branch:

```python
    if isinstance(payload, dict) and "digest" in payload:
        icon = "📈" if payload["kind"] == "jumps" else "📉"
        print(f"\n{icon} {payload['kind'].upper()} ({len(payload['digest'])})")
        print(format_digest(payload["digest"], payload.get("mode", "exalt")))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_scheduler.py tests/test_alerts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poe2bot/scheduler.py poe2bot/main.py tests/test_scheduler.py tests/test_alerts.py
git commit -m "feat: thread display mode through scheduler and notifiers"
```

---

### Task 4: `/pricebreakpoint` command + `/status` line

**Files:**
- Modify: `poe2bot/bot.py` (add `set_breakpoint_logic`, the `/pricebreakpoint` command, and a `/status` display line)
- Test: `tests/test_bot_commands.py` (add)

**Interfaces:**
- Consumes: `display.BREAKPOINT_SETTING`, `display.resolve_breakpoint`, `display.resolve_mode`, `display.exalt_per_chaos`; `store.set_setting`/`get_setting`.
- Produces: `async set_breakpoint_logic(store, exalts: float) -> str`; updated `status_text`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_bot_commands.py`

```python
import pytest
from poe2bot.store import Store
from poe2bot.bot import set_breakpoint_logic, status_text
from poe2bot.display import BREAKPOINT_SETTING


async def test_set_breakpoint_logic_persists(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    msg = await set_breakpoint_logic(s, 25.0)
    assert "25" in msg
    assert await s.get_setting(BREAKPOINT_SETTING) == "25.0"
    await s.close()


async def test_set_breakpoint_logic_rejects_non_positive(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    with pytest.raises(ValueError):
        await set_breakpoint_logic(s, 0.0)
    with pytest.raises(ValueError):
        await set_breakpoint_logic(s, -3.0)
    await s.close()


async def test_status_text_shows_breakpoint_and_mode(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("league", "L")
    await s.set_setting("anchor_divine", "240.0")       # 24 ex/chaos
    await s.set_setting("anchor_chaos_divine", "10.0")
    txt = await status_text(s)
    assert "chaos-mode" in txt
    assert "20" in txt                                  # default breakpoint shown
    await s.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_bot_commands.py -q`
Expected: FAIL — `ImportError: cannot import name 'set_breakpoint_logic'`.

- [ ] **Step 3a: Edit `poe2bot/bot.py` — imports**

Extend the `display` import (add one if none exists):

```python
from .display import BREAKPOINT_SETTING, resolve_breakpoint, resolve_mode, exalt_per_chaos
```

- [ ] **Step 3b: Add `set_breakpoint_logic`** (near `set_threshold_logic`)

```python
async def set_breakpoint_logic(store: Store, exalts: float) -> str:
    # exalts = how many Exalted 1 Chaos must be worth before output switches to Chaos/Divine.
    if exalts <= 0:
        raise ValueError(f"breakpoint must be > 0 exalts, got {exalts}")
    await store.set_setting(BREAKPOINT_SETTING, str(exalts))
    return f"Currency-display breakpoint set to {exalts:g} exalts per chaos."
```

- [ ] **Step 3c: Add the display line to `status_text`**

Before the `return`, read the anchor and resolve mode; append the line:

```python
    divine = float(await store.get_setting("anchor_divine") or "1.0")
    chaos_divine = float(await store.get_setting("anchor_chaos_divine") or "1.0")
    bp = await resolve_breakpoint(store)
    mode = await resolve_mode(store, divine, chaos_divine)
    ratio = exalt_per_chaos(divine, chaos_divine)
    display_line = f"Display: {mode}-mode (breakpoint {bp:g} ex/chaos, now {ratio:.3g})"
    return (f"League: {league}\nScanning: {cats}\nLast poll: {last_poll}\n"
            f"Per-category alert cap (K): {top_k}\n"
            f"Alert channel: {alert_disp}\nHealth channel: {health_disp}\n{display_line}")
```

(Replace the existing `return (...)`; keep the prior lines unchanged.)

- [ ] **Step 3d: Register the command** (beside `threshold_cmd` in `build_bot`)

```python
    @bot.tree.command(name="pricebreakpoint",
                      description="Set the 1-chaos≥N-exalts breakpoint that switches display units")
    @app_commands.default_permissions(manage_guild=True)
    async def pricebreakpoint_cmd(interaction: discord.Interaction, exalts: float):
        try:
            msg = await set_breakpoint_logic(store, exalts)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.send_message(msg, ephemeral=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_bot_commands.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS (all prior tests + the new ones; existing exalt-mode digest output unchanged).

- [ ] **Step 6: Commit**

```bash
git add poe2bot/bot.py tests/test_bot_commands.py
git commit -m "feat: /pricebreakpoint command and /status display-mode line"
```

---

## Self-Review notes

- **Spec coverage:** display.py (Task 1) ✓, digest mode (Task 2) ✓, scheduler+notifier stamping (Task 3) ✓, command + status (Task 4) ✓. `/price`/`format_price`/`--once`/schema deliberately unchanged ✓.
- **Type consistency:** `mode` is `str` everywhere; `MODE_COLS` shape fixed in the Global Constraints and consumed identically in Task 2; payload `"mode"` key produced in Task 3 and read with `.get(..., "exalt")` default by both notifiers.
- **No placeholders.** Every code step is complete and runnable.
