from __future__ import annotations
import asyncio, os, sys, time
from collections.abc import Mapping
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import Settings
from .store import Store
from .models import LiquidityTier
from .sources.poe2scout import Poe2ScoutClient
from .bot import LeagueService, ItemService, build_bot, resolve_channel_id
from .scheduler import poll_once
from .detector.engine import DetectConfig
from .health import CircuitBreaker, ping_dead_man
from .alerts import to_embed, overflow_line, format_alert_lines
from .signals import to_currencies


def build_notifier(bot, store, settings):
    """Resolve the target channel from the DB at SEND time (so /setchannel takes effect
    immediately, no restart), falling back to the env defaults."""
    async def notify(payload):
        if isinstance(payload, dict) and "health" in payload:
            hid = await resolve_channel_id(store, "health_channel_id", settings.health_channel_id)
            aid = await resolve_channel_id(store, "alert_channel_id", settings.alert_channel_id)
            target = hid or aid
            ch = bot.get_channel(target) if target else None
            if ch is not None:
                await ch.send(f"⚠️ pipeline health: {payload['health']}")
            return
        aid = await resolve_channel_id(store, "alert_channel_id", settings.alert_channel_id)
        channel = bot.get_channel(aid) if aid else None
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
    item_service = ItemService(client, store)
    bot = build_bot(store, league_service, item_service, settings)
    notify = build_notifier(bot, store, settings)
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
        divine = float(await store.get_setting("anchor_divine") or "1.0")
        chaos_divine = float(await store.get_setting("anchor_chaos_divine") or "1.0")
        cur = await store._db.execute(
            "SELECT name, price_exalt, liq_tier, volume FROM obs "
            "WHERE src_ts=(SELECT MAX(src_ts) FROM obs) ORDER BY price_exalt DESC LIMIT 5")
        rows = await cur.fetchall()
        this_poll = await (await store._db.execute(
            "SELECT COUNT(*) c FROM obs WHERE src_ts=(SELECT MAX(src_ts) FROM obs)")).fetchone()
        tot = await (await store._db.execute("SELECT COUNT(*) c FROM obs")).fetchone()
        print(f"\nLeague: {league}")
        print(f"Items ingested this poll: {this_poll['c']} (total ledger rows: {tot['c']}), alerts fired: {n}")
        print("Top items by price (Exalted / Divine / Chaos):")
        for r in rows:
            px, pdiv, pchaos = to_currencies(r["price_exalt"], divine, chaos_divine)
            tier = LiquidityTier(r["liq_tier"]).name
            flag = " ⚠low-liq" if tier == "LOW" else ""
            print(f"  {r['name']:<26} {px:>14,.4g} ex | {pdiv:>10,.4g} div | {pchaos:>12,.4g} chaos"
                  f"   [{tier}{flag}] vol/day={(r['volume'] or 0):,.0f}")
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
