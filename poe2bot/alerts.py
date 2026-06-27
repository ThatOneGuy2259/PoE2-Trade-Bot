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
