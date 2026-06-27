from __future__ import annotations
import asyncio
import logging
import time
import discord
from discord import app_commands

log = logging.getLogger(__name__)
from discord.ext import commands
from .store import Store
from .sources.poe2scout import Poe2ScoutClient
from .models import LiquidityTier
from .signals import wfs_phase1, to_currencies


# Preset categories for /threshold and /categories autocomplete. (api_id, label) pairs,
# from poe2scout's per-league Items/Categories (CurrencyCategories — the set reachable
# through the Currencies/ByCategory endpoint the poller uses). These rarely change, so a
# static list is fine. NOTE: Phase 1 polls only `currency`; the rest are presets ready for
# multi-category scanning (Phase 2). Discord caps a choices/autocomplete list at 25 (17 here).
CATEGORIES: list[tuple[str, str]] = [
    ("currency", "Currency"), ("fragments", "Fragments"), ("runes", "Runes"),
    ("essences", "Essences"), ("ultimatum", "Soul Cores"),
    ("expedition", "Expedition Coinage & Artifacts"), ("ritual", "Ritual Omens"),
    ("vaultkeys", "Reliquary Keys"), ("breach", "Breach"), ("abyss", "Abyssal Bones"),
    ("uncutgems", "Uncut Gems"), ("lineagesupportgems", "Lineage Support Gems"),
    ("delirium", "Delirium"), ("incursion", "Incursion"), ("idol", "Idols"),
    ("verisium", "Verisium"), ("vaal", "Vaal"),
]


class LeagueService:
    def __init__(self, client: Poe2ScoutClient, ttl_s: int = 86400):
        self._client = client
        self._ttl = ttl_s
        self._cache: list[str] = []
        self._fetched_at: int | None = None

    async def refresh(self, now_ts: int) -> list[str]:
        self._cache = await self._client.get_leagues()
        self._fetched_at = now_ts
        return self._cache

    async def available(self, now_ts: int) -> list[str]:
        if self._fetched_at is None or (now_ts - self._fetched_at) >= self._ttl:
            return await self.refresh(now_ts)
        return self._cache


class ItemService:
    """Caches the active league's currency catalog as (display_name, api_id) pairs for
    /price autocomplete. Re-fetches when the TTL lapses OR the league changes. Returns []
    when no league is set yet (so autocomplete is empty, not an error)."""

    def __init__(self, client: Poe2ScoutClient, store: Store, ttl_s: int = 3600):
        self._client = client
        self._store = store
        self._ttl = ttl_s
        self._cache: list[tuple[str, str]] = []
        self._fetched_at: int | None = None
        self._league: str | None = None

    async def refresh(self, league: str, now_ts: int) -> list[tuple[str, str]]:
        data = await self._client.get_currency_overview(league)
        pairs: list[tuple[str, str]] = []
        for it in data.get("Items", []):
            api_id = it.get("ApiId")
            if api_id is None:
                continue
            api_id = str(api_id)
            pairs.append((it.get("Text") or api_id, api_id))   # value=ApiId == store item_id
        self._cache, self._fetched_at, self._league = pairs, now_ts, league
        return pairs

    async def available(self, now_ts: int) -> list[tuple[str, str]]:
        league = await self._store.get_setting("league")
        if not league:
            return []
        stale = self._fetched_at is None or (now_ts - self._fetched_at) >= self._ttl
        if stale or league != self._league:
            return await self.refresh(league, now_ts)
        return self._cache


def sync_target_guilds(guild_id: int | None, joined_guild_ids: list[int]) -> list[int]:
    """Which guilds get instant slash-command sync: the explicit DISCORD_GUILD_ID if set,
    else every guild the bot has joined (auto, no config). Empty -> caller does a global
    sync (the ~1h-propagation default)."""
    if guild_id:
        return [guild_id]
    return list(joined_guild_ids)


async def sync_commands(tree, joined_guild_ids: list[int], guild_id: int | None) -> dict:
    """Push slash commands so they appear instantly.

    Per target guild: copy the globals into the guild and sync (instant). The global copies
    are cleared ONLY if at least one guild synced — otherwise a wrong/inaccessible guild id
    would both fail to sync AND strand the bot with no commands. With no targets at all, fall
    back to a global sync. Returns {"mode", "synced", "failed"} for logging/tests."""
    targets = sync_target_guilds(guild_id, joined_guild_ids)
    if not targets:
        await tree.sync()
        return {"mode": "global", "synced": [], "failed": []}
    synced: list[int] = []
    failed: list[int] = []
    for gid in targets:
        try:
            tree.copy_global_to(guild=discord.Object(id=gid))
            await tree.sync(guild=discord.Object(id=gid))
            synced.append(gid)
        except discord.HTTPException as e:
            failed.append(gid)
            log.warning("slash-command sync to guild %s failed: %s", gid, e)
    if synced:
        tree.clear_commands(guild=None)   # drop global copies so commands don't show twice
        await tree.sync()
    else:
        log.warning("all guild syncs failed (%s); leaving global commands intact", failed)
    return {"mode": "guild", "synced": synced, "failed": failed}


def filter_item_choices(pairs: list[tuple[str, str]], current: str,
                        limit: int = 25) -> list[tuple[str, str]]:
    """Substring-match (case-insensitive) `current` against each item's name OR api_id.
    Empty `current` returns everything. Caps at `limit` (Discord's max is 25)."""
    q = (current or "").lower()
    out = [(name, api_id) for (name, api_id) in pairs
           if not q or q in name.lower() or q in api_id.lower()]
    return out[:limit]


def filter_category_choices(current: str, limit: int = 25) -> list[tuple[str, str]]:
    """Preset-category autocomplete for the comma-separated /categories field.

    Completes the LAST comma token against CATEGORIES (api_id or label, case-insensitive),
    preserves earlier tokens verbatim, and skips categories already chosen. Returns
    (display, value) pairs where both are the full comma string the field becomes."""
    head, sep, tail = current.rpartition(",")
    prefix = f"{head}," if sep else ""
    chosen = {t.strip().lower() for t in head.split(",") if t.strip()}
    q = tail.strip().lower()
    out: list[tuple[str, str]] = []
    for api_id, label in CATEGORIES:
        if api_id.lower() in chosen:
            continue
        if not q or q in api_id.lower() or q in label.lower():
            value = f"{prefix}{api_id}"
            out.append((value, value))
    return out[:limit]


async def setleague_logic(store: Store, leagues: list[str], chosen: str) -> str:
    if chosen not in leagues:
        raise ValueError(f"'{chosen}' is not in the current league list")
    await store.set_setting("league", chosen)
    return f"League set to **{chosen}**."


async def set_categories_logic(store: Store, categories: list[str]) -> str:
    await store.set_setting("categories", ",".join(categories))
    return f"Scanning categories: {', '.join(categories)}"


async def set_threshold_logic(store: Store, category: str, spike_pct: float) -> str:
    await store.set_setting(f"thr:{category}", str(spike_pct))
    return f"Threshold for {category} set to {spike_pct:.0%}"


async def set_alert_channel_logic(store: Store, channel_id: int) -> str:
    await store.set_setting("alert_channel_id", str(channel_id))
    return f"✅ Price/demand alerts will now post in <#{channel_id}>."


async def set_health_channel_logic(store: Store, channel_id: int) -> str:
    await store.set_setting("health_channel_id", str(channel_id))
    return f"✅ Pipeline-health messages will now post in <#{channel_id}>."


async def resolve_channel_id(store: Store, key: str, fallback: int | None) -> int | None:
    """Channel set live via a command wins; otherwise fall back to the env default."""
    v = await store.get_setting(key)
    return int(v) if v else fallback


async def price_text(store: Store, item_id: str) -> str:
    obs = await store.last_observation(item_id)
    if obs is None:
        return f"No data for `{item_id}` yet."
    divine = float(await store.get_setting("anchor_divine") or "1.0")
    chaos_divine = float(await store.get_setting("anchor_chaos_divine") or "1.0")
    px, pdiv, pchaos = to_currencies(obs.price_exalt, divine, chaos_divine)
    vol = obs.volume or 0.0
    wfs = wfs_phase1(obs.price_exalt, obs.liq_tier.gate, max(divine, 1e-9), vol)
    note = "  ⚠ low liquidity" if obs.liq_tier == LiquidityTier.LOW else ""
    return (f"**{obs.name}** — {px:,.4g} ex | {pdiv:,.4g} div | {pchaos:,.4g} chaos\n"
            f"Liquidity: {obs.liq_tier.name}{note} | daily volume: {vol:,.0f} | WFS: {wfs:.3g}")


async def topmovers_text(store: Store, n: int) -> str:
    cur = await store._db.execute(
        "SELECT item_id, cls, direction, magnitude FROM alert_log WHERE fired=1 "
        "ORDER BY alert_id DESC LIMIT ?", (n,))
    rows = await cur.fetchall()
    if not rows:
        return "(no movers yet)"
    lines = [f"{r['item_id']}: {r['cls']} {r['direction']} ({r['magnitude']:+.2f} log)" for r in rows]
    return "\n".join(lines)


async def status_text(store: Store) -> str:
    league = await store.get_setting("league") or "(unset)"
    last_poll = await store.get_setting("last_poll_ts") or "(never)"
    top_k = await store.get_setting("top_k") or "8"
    alert_ch = await store.get_setting("alert_channel_id")
    health_ch = await store.get_setting("health_channel_id")
    alert_disp = f"<#{alert_ch}>" if alert_ch else "(env default / run /setchannel)"
    health_disp = f"<#{health_ch}>" if health_ch else "(env default / run /sethealthchannel)"
    return (f"League: {league}\nLast poll: {last_poll}\nPer-poll alert cap (K): {top_k}\n"
            f"Alert channel: {alert_disp}\nHealth channel: {health_disp}")


def build_bot(store: Store, league_service: LeagueService, item_service: ItemService,
              settings) -> commands.Bot:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        # Sync once per process: on_ready can re-fire on reconnect/RESUME, and command-sync
        # endpoints are heavily rate-limited. Guild-scoped sync propagates instantly (vs. up
        # to ~1h for global); target the explicit DISCORD_GUILD_ID, else auto-detect joined
        # guilds. bot.guilds is only populated here (not in setup_hook), so this lives here.
        if not getattr(bot, "_commands_synced", False):
            guild_id = getattr(settings, "discord_guild_id", None) if settings else None
            try:
                result = await sync_commands(bot.tree, [g.id for g in bot.guilds], guild_id)
                log.info("slash-command sync: %s", result)
            except discord.HTTPException as e:
                log.warning("command sync failed: %s", e)
            bot._commands_synced = True
        # Best-effort cache pre-warm so the first /price autocomplete is already hot.
        try:
            await item_service.available(int(time.time()))
        except Exception:
            pass

    async def _league_autocomplete(interaction: discord.Interaction, current: str):
        leagues = await league_service.available(int(time.time()))
        return [app_commands.Choice(name=l, value=l)
                for l in leagues if current.lower() in l.lower()][:25]

    async def _item_autocomplete(interaction: discord.Interaction, current: str):
        # Autocomplete must answer within Discord's ~3s budget. Steady state is a cached,
        # in-memory filter; the cold/expired/league-change path fetches the catalog, so
        # bound it and degrade to "no suggestions" rather than raise on a slow/failed fetch.
        try:
            pairs = await asyncio.wait_for(item_service.available(int(time.time())), timeout=2.0)
        except Exception:   # timeout, network, or parse — degrade to no suggestions
            return []
        return [app_commands.Choice(name=name[:100], value=api_id)
                for (name, api_id) in filter_item_choices(pairs, current)]

    async def _category_autocomplete(interaction: discord.Interaction, current: str):
        # Presets are static/in-memory; drop (don't slice) any value over Discord's 100-char
        # limit so a long comma chain can't submit a token truncated mid-word.
        return [app_commands.Choice(name=val, value=val)
                for (disp, val) in filter_category_choices(current) if len(val) <= 100]

    @bot.tree.command(name="leagues", description="List currently available leagues")
    async def leagues_cmd(interaction: discord.Interaction):
        leagues = await league_service.available(int(time.time()))
        await interaction.response.send_message(", ".join(leagues) or "(none)", ephemeral=True)

    @bot.tree.command(name="setleague", description="Set the active league")
    @app_commands.autocomplete(name=_league_autocomplete)
    async def setleague_cmd(interaction: discord.Interaction, name: str):
        leagues = await league_service.available(int(time.time()))
        try:
            msg = await setleague_logic(store, leagues, name)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="status", description="Show bot status")
    async def status_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(await status_text(store), ephemeral=True)

    @bot.tree.command(name="setchannel", description="Send price/demand alerts to THIS channel")
    @app_commands.default_permissions(manage_guild=True)
    async def setchannel_cmd(interaction: discord.Interaction):
        msg = await set_alert_channel_logic(store, interaction.channel_id)
        await interaction.response.send_message(msg)

    @bot.tree.command(name="sethealthchannel", description="Send pipeline-health messages to THIS channel")
    @app_commands.default_permissions(manage_guild=True)
    async def sethealthchannel_cmd(interaction: discord.Interaction):
        msg = await set_health_channel_logic(store, interaction.channel_id)
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="categories", description="Set item categories to scan (comma-separated)")
    @app_commands.autocomplete(categories=_category_autocomplete)
    async def categories_cmd(interaction: discord.Interaction, categories: str):
        cats = [c.strip() for c in categories.split(",") if c.strip()]
        msg = await set_categories_logic(store, cats)
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="threshold", description="Set spike threshold for a category")
    @app_commands.choices(category=[app_commands.Choice(name=label, value=api_id)
                                    for api_id, label in CATEGORIES])
    async def threshold_cmd(interaction: discord.Interaction,
                            category: app_commands.Choice[str], spike_pct: float):
        msg = await set_threshold_logic(store, category.value, spike_pct)
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="price", description="Current value of an item")
    @app_commands.autocomplete(item_id=_item_autocomplete)
    async def price_cmd(interaction: discord.Interaction, item_id: str):
        await interaction.response.send_message(await price_text(store, item_id))

    @bot.tree.command(name="topmovers", description="Show top movers from recent alerts")
    async def topmovers_cmd(interaction: discord.Interaction, n: int = 5):
        await interaction.response.send_message(await topmovers_text(store, n))

    return bot
