from poe2bot.alerts import (format_alert_lines, overflow_line, humanize, format_digest,
                            to_digest_embed, _digest_row)
from poe2bot.models import AlertEvent, LiquidityTier
import discord

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


def test_humanize():
    assert humanize(250) == "250"
    assert humanize(2500) == "2.5k"
    assert humanize(184000) == "184k"
    assert humanize(1_844_296) == "1.84M"
    assert humanize(0.004) == "0.004"


def test_format_digest_table():
    evs = [_ev(price_exalt=250.0, price_chaos=2520.0, pct=0.45),
           _ev(price_exalt=560000.0, price_chaos=5.6e6, pct=0.30,
               tier=LiquidityTier.LOW, low_confidence=True)]
    table = format_digest(evs)
    lines = table.splitlines()
    assert lines[0].split() == ["Item", "Ex", "Chaos", "Info"]   # header row, in order
    assert "Divine Orb" in table
    assert "250" in table and "2.5k" in table                    # humanized cells
    assert "+45%" in table and "+30%" in table                   # Info = % move
    assert "⚠" in table                                          # low-liquidity flag on the 2nd row


def test_format_digest_demand_label():
    table = format_digest([_ev(cls="DEMAND_COLLAPSE", direction="down", pct=-0.6)])
    assert "-60%" in table and "vol" in table                    # volume drop marked


async def test_notifier_routes_digests_and_overflow(tmp_path):
    from types import SimpleNamespace
    from poe2bot.store import Store
    from poe2bot.main import build_notifier
    s = await Store.open(str(tmp_path / "t.db"))
    await s.set_setting("alert_channel_id", "123")
    sent = []
    class _Ch:
        async def send(self, content=None, embed=None): sent.append(embed.title if embed else content)
    class _Bot:
        def get_channel(self, cid): return _Ch() if cid == 123 else None
    notify = build_notifier(_Bot(), s, SimpleNamespace(alert_channel_id=None, health_channel_id=None))
    await notify({"digest": [_ev()], "kind": "jumps"})
    await notify({"digest": [_ev(cls="CRASH", direction="down", pct=-0.4)], "kind": "drops"})
    await notify({"overflow": 3})
    assert any("Jumps" in str(x) for x in sent)                  # up events -> jumps message
    assert any("Drops" in str(x) for x in sent)                  # down events -> drops message
    assert any("3 more" in str(x) for x in sent)                 # overflow still a plain line
    await s.close()


def test_to_digest_embed_colors_and_title():
    up = to_digest_embed([_ev()], "jumps")
    assert "Jumps (1)" in up.title and "📈" in up.title
    assert up.color == discord.Color.green()
    down = to_digest_embed([_ev(cls="CRASH", direction="down", pct=-0.4)], "drops")
    assert "Drops (1)" in down.title and down.color == discord.Color.red()
    assert "```" in up.description                                # monospace code block table


# --- mode-aware digest columns (Ex/Chaos vs Chaos/Div) ----------------------

def test_digest_exalt_mode_headers_and_cells():
    out = format_digest([_ev(price_exalt=100.0, price_chaos=50.0, price_div=0.5)], mode="exalt")
    header, row = out.splitlines()[0], out.splitlines()[1]
    assert "Ex" in header and "Chaos" in header and "Div" not in header
    assert "100" in row and "50" in row          # price_exalt primary, price_chaos secondary


def test_digest_chaos_mode_headers_and_cells():
    out = format_digest([_ev(price_exalt=100.0, price_chaos=50.0, price_div=0.5)], mode="chaos")
    header, row = out.splitlines()[0], out.splitlines()[1]
    assert "Chaos" in header and "Div" in header and "Ex" not in header
    assert "50" in row and "0.5" in row          # price_chaos primary, price_div secondary


def test_digest_default_mode_is_exalt():
    assert format_digest([_ev()]) == format_digest([_ev()], mode="exalt")


def test_digest_row_selects_attributes_by_mode():
    e = _ev(price_exalt=100.0, price_chaos=50.0, price_div=0.5)
    ex_row, ch_row = _digest_row(e, "exalt"), _digest_row(e, "chaos")
    assert ex_row[1] == "100" and ex_row[2] == "50"     # ex, chaos
    assert ch_row[1] == "50" and ch_row[2] == "0.5"     # chaos, div


def test_to_digest_embed_threads_mode():
    assert "Div" in to_digest_embed([_ev()], "jumps", mode="chaos").description
