from __future__ import annotations
import asyncio, os, sys, time
from collections.abc import Mapping
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import Settings
from .store import Store
from .models import LiquidityTier
from .sources.poe2scout import Poe2ScoutClient
from .bot import LeagueService, build_bot
from .scheduler import poll_once
from .detector.engine import DetectConfig
from .health import CircuitBreaker, ping_dead_man
from .alerts import to_embed, overflow_line, format_alert_lines


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


async def _stdout_notify(payload):
    if isinstance(payload, dict):
        kind = "health" if "health" in payload else "overflow"
        print(f"  [{kind}] {payload}")
    else:
        title, body = format_alert_lines(payload)
        print(f"\n{title}\n{body}\n")


async def run_once(env: Mapping[str, str], league: str | None = None) -> None:
    """Dry run: one real poll against live poe2scout, alerts printed to stdout.

    Requires NO Discord credentials — it exercises fetch -> normalize -> store -> detect
    -> alert formatting against the real API. On a cold ledger no alerts fire (detection
    needs a baseline); the summary proves ingestion works end to end.
    """
    db_path = env.get("DB_PATH", "./poe2bot.db")
    ua = env.get("POE2SCOUT_UA", "poe2bot/0.1 (contact: unset)")
    store = await Store.open(db_path)
    session = aiohttp.ClientSession()
    client = Poe2ScoutClient(session, ua)
    breaker = CircuitBreaker()
    cfg = DetectConfig()
    try:
        league = league or await store.get_setting("league") or await client.get_current_league()
        if not league:
            print("Could not determine a league from poe2scout.")
            return
        await store.set_setting("league", league)
        n = await poll_once(store, client, cfg, int(time.time()), breaker, _stdout_notify)
        cur = await store._db.execute(
            "SELECT name, price_exalt, liq_tier, volume FROM obs "
            "WHERE src_ts=(SELECT MAX(src_ts) FROM obs) ORDER BY price_exalt DESC LIMIT 5")
        rows = await cur.fetchall()
        this_poll = await (await store._db.execute(
            "SELECT COUNT(*) c FROM obs WHERE src_ts=(SELECT MAX(src_ts) FROM obs)")).fetchone()
        tot = await (await store._db.execute("SELECT COUNT(*) c FROM obs")).fetchone()
        print(f"\nLeague: {league}")
        print(f"Items ingested this poll: {this_poll['c']} (total ledger rows: {tot['c']}), alerts fired: {n}")
        print("Top items by price (Exalted-equiv):")
        for r in rows:
            print(f"  {r['name']:<26} {r['price_exalt']:>16.4f}  "
                  f"tier={LiquidityTier(r['liq_tier']).name:<4} vol={r['volume']}")
        if n == 0:
            print("\n(0 alerts is expected on a cold ledger — the detector needs a stored "
                  "baseline before it can flag a move. Run the bot continuously to build one.)")
    finally:
        await session.close()
        await store.close()


if __name__ == "__main__":
    if "--once" in sys.argv:
        asyncio.run(run_once(os.environ))
    else:
        asyncio.run(amain(os.environ))
