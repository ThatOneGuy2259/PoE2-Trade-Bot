from poe2bot.alerts import format_alert_lines, overflow_line
from poe2bot.models import AlertEvent, LiquidityTier

def _ev(cls="JUMP", direction="up", pct=0.30, trade_id="divine", tier=LiquidityTier.HIGH,
        low_confidence=False, price_exalt=100.0, price_div=0.25, price_chaos=2.5):
    return AlertEvent(item_id="divine", name="Divine Orb", cls=cls, direction=direction,
                      magnitude=0.26, pct_move=pct, baseline=0.0, current=0.26, severity=0.26,
                      liq_tier=tier, trade_id=trade_id, wfs=1.23,
                      price_exalt=price_exalt, price_div=price_div, price_chaos=price_chaos,
                      low_confidence=low_confidence)

def test_title_and_body():
    title, body = format_alert_lines(_ev())
    assert "JUMP" in title and "Divine Orb" in title
    assert "+30" in body and "HIGH" in body

def test_shows_three_currencies():
    _, body = format_alert_lines(_ev(price_exalt=100.0, price_div=0.25, price_chaos=2.5))
    assert "100" in body and "ex" in body and "0.25" in body and "div" in body and "2.5" in body and "chaos" in body

def test_low_confidence_flag():
    _, body = format_alert_lines(_ev(tier=LiquidityTier.LOW, low_confidence=True))
    assert "low liquidity" in body.lower()
    # and a normal HIGH-liquidity alert does NOT carry the warning
    _, body2 = format_alert_lines(_ev())
    assert "low liquidity" not in body2.lower()

def test_demand_collapse_wording():
    title, body = format_alert_lines(_ev(cls="DEMAND_COLLAPSE", direction="down", pct=-0.6))
    assert "DEMAND" in title.upper()
    assert "-60" in body and "Volume drop" in body

def test_overflow_line():
    assert overflow_line(5) == "+5 more movers this cycle"
