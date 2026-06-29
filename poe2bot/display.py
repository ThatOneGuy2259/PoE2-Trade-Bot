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
