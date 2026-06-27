from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from .store import Store
from .sources.poe2scout import Poe2ScoutClient


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

    return bot
