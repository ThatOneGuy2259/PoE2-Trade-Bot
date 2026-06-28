from __future__ import annotations
import discord
from .models import AlertEvent

_ICON = {"JUMP": "📈", "CRASH": "📉", "DEMAND_COLLAPSE": "🥶"}


def format_price(event: AlertEvent) -> str:
    """Current price in the three primary currencies."""
    return (f"{event.price_exalt:,.4g} ex  |  "
            f"{event.price_div:,.4g} div  |  "
            f"{event.price_chaos:,.4g} chaos")


def format_alert_lines(event: AlertEvent) -> tuple[str, str]:
    icon = _ICON.get(event.cls, "❓")
    title = f"{icon} {event.cls} — {event.name}"
    change_label = "Volume drop" if event.cls == "DEMAND_COLLAPSE" else "Change"
    lines = [f"{change_label}: {event.pct_move:+.0%}",
             f"Price: {format_price(event)}",
             f"Liquidity: {event.liq_tier.name}",
             f"WFS: {event.wfs:.3g}"]
    if event.low_confidence:
        lines.append("⚠ low liquidity — price may be unreliable")
    if event.trade_id:
        lines.append(f"Trade: https://www.pathofexile.com/trade2/search/poe2/{event.trade_id}")
    return title, "\n".join(lines)

def overflow_line(n: int) -> str:
    return f"+{n} more movers this cycle"

def to_embed(event: AlertEvent) -> discord.Embed:
    title, body = format_alert_lines(event)
    color = discord.Color.green() if event.direction == "up" else discord.Color.red()
    return discord.Embed(title=title, description=body, color=color)


# --- compact digest: one table of jumps, one of drops, per poll -------------

def humanize(x: float) -> str:
    """Compact number for table cells: 250, 2.5k, 184k, 1.84M, 0.004."""
    ax = abs(x)
    if ax >= 1e9: return f"{x / 1e9:.2f}B"
    if ax >= 1e6: return f"{x / 1e6:.2f}M"
    if ax >= 1e4: return f"{x / 1e3:.0f}k"
    if ax >= 1e3: return f"{x / 1e3:.1f}k"
    if ax >= 1:   return f"{x:.3g}"
    return f"{x:.2g}"


_DIGEST_HEADERS = ("Item", "Ex", "Chaos", "Info")


def _digest_row(e: AlertEvent) -> tuple[str, str, str, str]:
    info = f"{e.pct_move:+.0%}"
    if e.cls == "DEMAND_COLLAPSE":
        info += " vol"                       # the move is a volume drop, not a price move
    if e.low_confidence:
        info += " ⚠"                         # thin market — price may be unreliable
    return (e.name[:22], humanize(e.price_exalt), humanize(e.price_chaos), info)


def format_digest(events: list[AlertEvent], max_rows: int = 40) -> str:
    """A monospace table (Item · Ex · Chaos · Info) of one direction's movers."""
    rows = [_digest_row(e) for e in events[:max_rows]]
    cols = list(zip(*([_DIGEST_HEADERS] + rows)))          # column-wise for width sizing
    w = [max(len(str(c)) for c in col) for col in cols]
    def fmt(r): return f"{r[0]:<{w[0]}}  {r[1]:>{w[1]}}  {r[2]:>{w[2]}}  {r[3]:<{w[3]}}"
    lines = [fmt(_DIGEST_HEADERS)] + [fmt(r) for r in rows]
    if len(events) > max_rows:
        lines.append(f"…and {len(events) - max_rows} more")
    return "\n".join(lines)


def to_digest_embed(events: list[AlertEvent], kind: str) -> discord.Embed:
    """One embed per direction: kind='jumps' (green 📈) or 'drops' (red 📉)."""
    icon = "📈" if kind == "jumps" else "📉"
    color = discord.Color.green() if kind == "jumps" else discord.Color.red()
    title = f"{icon} {kind.capitalize()} ({len(events)})"
    return discord.Embed(title=title, description="```\n" + format_digest(events) + "\n```",
                         color=color)
