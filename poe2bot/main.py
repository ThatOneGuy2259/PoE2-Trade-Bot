from __future__ import annotations
import asyncio, os, time
from collections.abc import Mapping
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import Settings
from .store import Store
from .sources.poe2scout import Poe2ScoutClient
from .bot import LeagueService, build_bot
from .scheduler import poll_once
from .detector.engine import DetectConfig
from .health import CircuitBreaker, ping_dead_man
from .alerts import to_embed, overflow_line


def build_notifier(bot, alert_channel_id: int, health_channel_id: int | None):
    async def notify(payload):
        if isinstance(payload, dict) and "health" in payload:
            ch = bot.get_channel(health_channel_id or alert_channel_id)
            if ch is not None:
                await ch.send(f"⚠️ pipeline health: {payload['health']}")
            return
        channel = bot.get_channel(alert_channel_id)
        if channel is None:
            return
        if isinstance(payload, dict) and "overflow" in payload:
            await channel.send(overflow_line(payload["overflow"]))
        else:
            await channel.send(embed=to_embed(payload))
    return notify


async def amain(env: Mapping[str, str]) -> None:
    settings = Settings.from_env(env)
    store = await Store.open(settings.db_path)
    session = aiohttp.ClientSession()
    client = Poe2ScoutClient(session, settings.poe2scout_ua)
    league_service = LeagueService(client)
    bot = build_bot(store, league_service, settings)
    notify = build_notifier(bot, settings.alert_channel_id, settings.health_channel_id)
    breaker = CircuitBreaker()
    cfg = DetectConfig()

    scheduler = AsyncIOScheduler()

    async def poll_job():
        await poll_once(store, client, cfg, int(time.time()), breaker, notify)
        await ping_dead_man(session, settings.dead_man_url)

    async def prune_job():
        await store.prune(int(time.time()))

    scheduler.add_job(poll_job, "interval", minutes=settings.poll_interval_min)
    scheduler.add_job(prune_job, "interval", hours=24)
    scheduler.start()
    try:
        await bot.start(settings.discord_token)
    finally:
        await session.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(amain(os.environ))
