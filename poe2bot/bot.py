from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from .store import Store
from .sources.poe2scout import Poe2ScoutClient
from .signals import wfs_phase1


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


async def price_text(store: Store, item_id: str) -> str:
    obs = await store.last_observation(item_id)
    if obs is None:
        return f"No data for `{item_id}` yet."
    divine = 1.0  # WFS rebasing uses the league anchor in the scheduler; /price shows raw tier+price
    vol = obs.volume or 0.0
    wfs = wfs_phase1(obs.price_exalt, obs.liq_tier.gate, max(divine, 1e-9), vol)
    return (f"**{obs.name}** — {obs.price_exalt:g} (Exalted-equiv)\n"
            f"Liquidity: {obs.liq_tier.name} | 24h volume: {vol:g} | WFS: {wfs:.3g}")


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
    return f"League: {league}\nLast poll: {last_poll}\nPer-poll alert cap (K): {top_k}"


def build_bot(store: Store, league_service: LeagueService, settings) -> commands.Bot:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        await bot.tree.sync()

    async def _league_autocomplete(interaction: discord.Interaction, current: str):
        import time
        leagues = await league_service.available(int(time.time()))
        return [app_commands.Choice(name=l, value=l)
                for l in leagues if current.lower() in l.lower()][:25]

    @bot.tree.command(name="leagues", description="List currently available leagues")
    async def leagues_cmd(interaction: discord.Interaction):
        import time
        leagues = await league_service.available(int(time.time()))
        await interaction.response.send_message(", ".join(leagues) or "(none)", ephemeral=True)

    @bot.tree.command(name="setleague", description="Set the active league")
    @app_commands.autocomplete(name=_league_autocomplete)
    async def setleague_cmd(interaction: discord.Interaction, name: str):
        import time
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

    @bot.tree.command(name="categories", description="Set item categories to scan")
    async def categories_cmd(interaction: discord.Interaction, categories: str):
        cats = [c.strip() for c in categories.split(",") if c.strip()]
        msg = await set_categories_logic(store, cats)
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="threshold", description="Set spike threshold for a category")
    async def threshold_cmd(interaction: discord.Interaction, category: str, spike_pct: float):
        msg = await set_threshold_logic(store, category, spike_pct)
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="price", description="Current value of an item")
    async def price_cmd(interaction: discord.Interaction, item_id: str):
        await interaction.response.send_message(await price_text(store, item_id))

    @bot.tree.command(name="topmovers", description="Show top movers from recent alerts")
    async def topmovers_cmd(interaction: discord.Interaction, n: int = 5):
        await interaction.response.send_message(await topmovers_text(store, n))

    return bot
