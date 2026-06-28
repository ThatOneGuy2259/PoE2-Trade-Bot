from __future__ import annotations

# Category registry — the single source of truth for which categories the bot can scan and
# which poe2scout endpoint serves each. Kept in this neutral, discord-free module so the core
# poll loop (scheduler.py) can read it without importing the Discord layer.
#
# Each entry is (api_id, label, family) where family ∈ {"currency", "uniques"}:
#   - "currency" categories are served by Currencies/ByCategory (same item shape as currency)
#   - "uniques"  categories are served by Uniques/ByCategory   (UniqueItemId/Name/Type shape)
# from poe2scout's per-league Items/Categories. These rarely change, so a static list is fine.
# 24 entries total — Discord caps a choices/autocomplete list at 25, so /threshold's static
# choices have one slot of headroom (switch it to autocomplete like /categories if it grows).
CATEGORIES: list[tuple[str, str, str]] = [
    # currency-family (Currencies/ByCategory)
    ("currency", "Currency", "currency"), ("fragments", "Fragments", "currency"),
    ("runes", "Runes", "currency"), ("essences", "Essences", "currency"),
    ("ultimatum", "Soul Cores", "currency"),
    ("expedition", "Expedition Coinage & Artifacts", "currency"),
    ("ritual", "Ritual Omens", "currency"), ("vaultkeys", "Reliquary Keys", "currency"),
    ("breach", "Breach", "currency"), ("abyss", "Abyssal Bones", "currency"),
    ("uncutgems", "Uncut Gems", "currency"),
    ("lineagesupportgems", "Lineage Support Gems", "currency"),
    ("delirium", "Delirium", "currency"), ("incursion", "Incursion", "currency"),
    ("idol", "Idols", "currency"), ("verisium", "Verisium", "currency"),
    ("vaal", "Vaal", "currency"),
    # uniques/equipment (Uniques/ByCategory)
    ("accessory", "Accessories", "uniques"), ("armour", "Armour", "uniques"),
    ("flask", "Flasks", "uniques"), ("jewel", "Jewels", "uniques"),
    ("map", "Maps", "uniques"), ("weapon", "Weapons", "uniques"),
    ("sanctum", "Sanctum Research", "uniques"),
]

_FAMILY_BY_ID = {api_id: family for api_id, _label, family in CATEGORIES}


def category_family(api_id: str) -> str | None:
    """The endpoint family ('currency' | 'uniques') for a category api_id, or None if it isn't a
    known category. Returning None (rather than raising) lets the poll loop skip an unknown/typo'd
    category gracefully."""
    return _FAMILY_BY_ID.get(api_id)
