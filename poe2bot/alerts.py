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
