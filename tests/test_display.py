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
