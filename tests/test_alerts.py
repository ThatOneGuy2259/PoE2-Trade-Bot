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
