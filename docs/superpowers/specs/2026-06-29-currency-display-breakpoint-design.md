# Currency-Display Breakpoint — Design

**Date:** 2026-06-29
**Status:** Approved (design); pending spec review

## Goal

Switch the currency units shown in price output once the economy inflates past a
threshold. While 1 chaos is worth fewer than N exalts, prices read in Exalted (with a
Chaos secondary), as today. Once 1 chaos is worth ≥ N exalts, Exalted has become too
granular/inflated to be useful, so output switches to Chaos (with a Divine secondary)
and drops Exalted.

Single threshold, two modes. Default N = 20. Configurable at runtime. Applies to the
**digest notification tables** — the only surface constrained to two currency columns.
`/price` and the `--once` stdout summary already print all three currencies on one line
(`ex | div | chaos`), so they are left unchanged.

## Background — how prices are represented today

- **Exalted is the base unit.** `Observation.price_exalt` is every item's price in Exalted.
- The league anchor carries `divine_exalt` (Exalted per Divine) and `chaos_divine`
  (Chaos per Divine), refreshed every poll and persisted as the settings
  `anchor_divine` / `anchor_chaos_divine`.
- `signals.to_currencies(price_exalt, divine_exalt, chaos_divine)` returns
  `(exalt, divine, chaos)`. `AlertEvent` already carries all three precomputed:
  `price_exalt`, `price_div`, `price_chaos`.
- The digest table (`alerts.py`) renders two currency columns: `Item | Ex | Chaos | Info`.
  `/price` (`bot.price_text`) and the `--once` stdout summary print all three on one line.

## The breakpoint formula

Chaos-per-Exalt = `chaos_divine / divine_exalt`, so **Exalted-per-Chaos = `divine_exalt / chaos_divine`**.

```
exalt_per_chaos(divine_exalt, chaos_divine) = divine_exalt / max(chaos_divine, eps)
mode = "chaos" if exalt_per_chaos >= breakpoint else "exalt"
```

`>= breakpoint` (default 20) flips to chaos-mode. The `eps` guard prevents a divide-by-zero
if `chaos_divine` is ever 0.

| Economy state | Mode | Columns (primary, secondary) |
|---|---|---|
| 1 chaos `<` 20 ex | `exalt` | **Ex** (`price_exalt`), Chaos (`price_chaos`) — today's behavior |
| 1 chaos `>=` 20 ex | `chaos` | **Chaos** (`price_chaos`), Div (`price_div`) — Exalted dropped |

Chaos is the bridge unit: it is the exalt-mode secondary and the chaos-mode primary, so the
displayed window slides up the ladder ex → chaos → div as the economy inflates. A third
(divine) tier is intentionally **not** built now (YAGNI); the column map below extends to it
trivially if ever wanted.

## Architecture

A new neutral module `poe2bot/display.py` owns the mode decision and the column map. It is
import-free of discord and of formatting concerns, so both the scheduler (core) and the
formatters (alerts) can use it. `AlertEvent` already carries all three prices, so the
formatters never recompute currency — they only **select** which two of the three to show,
keyed by `mode`.

**Mode is decided once, at the freshest anchor, and travels with the data:**

- **Scheduler** (`poll_once`) already holds the live `anchor`. It reads the breakpoint
  setting, computes `mode`, and stamps it into each digest payload:
  `{"digest": ups, "kind": "jumps", "mode": mode}`.
- **Notifiers** (`build_notifier`, `_stdout_notify`) read `payload.get("mode", "exalt")`
  and pass it through to the digest formatters.
- **`/status`** resolves mode itself (from the stored anchor + breakpoint settings) to show
  the current breakpoint and live mode. `/price`, the legacy embed, and the `--once` summary
  do not consult mode — they keep their three-currency line.

One formula, one column map; the digest path and `/status` resolve mode the same way.

### `poe2bot/display.py` (new)

```python
from __future__ import annotations

DEFAULT_CHAOS_EXALT_BREAKPOINT = 20.0   # 1 chaos worth >= this many exalts -> chaos-mode
BREAKPOINT_SETTING = "display_chaos_breakpoint"

# (header, AlertEvent attribute) for (primary, secondary), per mode.
MODE_COLS: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "exalt": (("Ex", "price_exalt"), ("Chaos", "price_chaos")),
    "chaos": (("Chaos", "price_chaos"), ("Div", "price_div")),
}

def exalt_per_chaos(divine_exalt: float, chaos_divine: float, eps: float = 1e-9) -> float:
    return divine_exalt / max(chaos_divine, eps)

def display_mode(divine_exalt: float, chaos_divine: float, breakpoint: float) -> str:
    return "chaos" if exalt_per_chaos(divine_exalt, chaos_divine) >= breakpoint else "exalt"
```

A helper resolves the configured breakpoint from the store with the default fallback:

```python
async def resolve_breakpoint(store) -> float:
    v = await store.get_setting(BREAKPOINT_SETTING)
    return float(v) if v else DEFAULT_CHAOS_EXALT_BREAKPOINT

async def resolve_mode(store, divine_exalt: float, chaos_divine: float) -> str:
    return display_mode(divine_exalt, chaos_divine, await resolve_breakpoint(store))
```

### `alerts.py` changes

- `_digest_row(e, mode)`, `format_digest(events, mode, max_rows=40)`,
  `to_digest_embed(events, kind, mode)` gain a `mode` parameter **defaulting to `"exalt"`**
  (so any caller/test not yet passing it keeps today's Ex/Chaos behavior — minimal churn).
- Headers and the two value cells are read from `MODE_COLS[mode]` via `getattr(e, attr)`.
  The header tuple becomes `(MODE_COLS[mode][0][0], MODE_COLS[mode][1][0])` plugged into the
  existing `("Item", <p>, <s>, "Info")` width-sizing logic.
- `format_price` and the legacy single-event embed path (`format_alert_lines` → `to_embed`)
  are **unchanged** — they keep the three-currency `ex | div | chaos` line.

### `scheduler.py` changes

After computing `anchor`, before building digests:

```python
from .display import resolve_mode
...
mode = await resolve_mode(store, anchor.divine_exalt, anchor.chaos_divine)
...
if ups:   await notify({"digest": ups,   "kind": "jumps", "mode": mode})
if downs: await notify({"digest": downs, "kind": "drops", "mode": mode})
```

### `main.py` changes

- `build_notifier.notify`: `await channel.send(embed=to_digest_embed(payload["digest"], payload["kind"], payload.get("mode", "exalt")))`.
- `_stdout_notify`: `format_digest(payload["digest"], payload.get("mode", "exalt"))`.
- `run_once` summary: **unchanged** — it keeps printing the three-currency top-items block.

### `bot.py` changes

- `price_text(store, item_id)`: **unchanged** — keeps the three-currency line.
- New `set_breakpoint_logic(store, exalts)`:
  ```python
  async def set_breakpoint_logic(store, exalts: float) -> str:
      if exalts <= 0:
          raise ValueError(f"breakpoint must be > 0 exalts, got {exalts}")
      await store.set_setting(BREAKPOINT_SETTING, str(exalts))
      return f"Currency-display breakpoint set to {exalts:g} exalts per chaos."
  ```
- New slash command `/pricebreakpoint <exalts: float>` (name open — see Open question 2),
  guarded with `@app_commands.default_permissions(manage_guild=True)` to match the other
  config-mutating commands. Command name: **`/pricebreakpoint`** (e.g. `/pricebreakpoint 20`).
- `status_text`: add a line — the current breakpoint and the live mode, e.g.
  `Display: chaos-mode (breakpoint 20 ex/chaos, now 24.1)`. Requires reading the anchor
  settings inside `status_text`.

### `store.py`

No schema change. `settings` is a generic key/value table; the breakpoint lives under
`display_chaos_breakpoint`. No migration.

## Edge cases

- **Cold/default anchor** (`divine=1.0`, `chaos=1.0`): ratio 1 < 20 → exalt-mode. Safe default.
- **`chaos_divine == 0`**: `eps` guard → no divide-by-zero (yields a large ratio → chaos-mode,
  which never occurs in practice but must not crash).
- **`breakpoint <= 0`**: rejected by `set_breakpoint_logic`.
- **Unset breakpoint setting**: `resolve_breakpoint` falls back to 20.0.
- **Default `mode` param**: the digest formatters default to `"exalt"`, so untouched call
  sites and existing tests keep today's `Ex | Chaos` output. Only the digest path changes;
  `/price`, the legacy embed, and the `--once` summary keep their three-currency line.

## Testing

- `tests/test_display.py` (new): `exalt_per_chaos` math; `display_mode` boundary
  (just-below / exactly-at / above 20 → exalt/chaos/chaos); `MODE_COLS` shape;
  `resolve_breakpoint` default + override; `resolve_mode` end-to-end against a store.
- `tests/test_alerts.py`: digest in both modes (header text swaps Ex→Chaos, Chaos→Div; cells
  read the right attributes); default-`mode` path preserves today's `Ex | Chaos` output.
- `tests/test_scheduler.py`: digest payloads carry `"mode"`, and its value matches the anchor
  (one fixture below breakpoint → "exalt", one at/above → "chaos").
- `tests/test_bot_commands.py`: `set_breakpoint_logic` happy path + `<= 0` rejection;
  `status_text` shows the breakpoint/mode line.
- `tests/test_smoke.py` / `main` stdout: digest payload extraction tolerates the new `"mode"`
  key (it already extracts via `"digest" in payload`).

## Files touched

- **Create:** `poe2bot/display.py`, `tests/test_display.py`
- **Modify:** `poe2bot/alerts.py`, `poe2bot/scheduler.py`, `poe2bot/main.py`,
  `poe2bot/bot.py`, and the tests named above.
- **No change:** `poe2bot/store.py` schema, `poe2bot/models.py`, `poe2bot/signals.py`.

## Resolved in review

1. **`/price` and the `--once` summary:** keep the three-currency line. Only the digest
   tables (the two-column surface) swap units.
2. **Command name:** `/pricebreakpoint`.

## Out of scope (YAGNI)

- A divine (third) display tier or a fully configurable breakpoint ladder.
- Per-item smart-unit selection.
- Admin-gating the other pre-existing config commands (separate tracked task).
