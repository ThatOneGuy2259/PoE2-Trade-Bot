from __future__ import annotations
import logging
from .models import Anchor
from .sources.normalize import normalize_currency, normalize_uniques
from .detector.engine import detect, DetectConfig
from .categories import category_family

log = logging.getLogger(__name__)


def _scanned_categories(raw: str | None) -> list[str]:
    """Parse the `categories` setting into an ordered, de-duplicated list; default to
    ['currency'] when unset/empty so behavior matches single-category Phase 1."""
    seen: list[str] = []
    for c in (raw or "").split(","):
        c = c.strip()
        if c and c not in seen:
            seen.append(c)
    return seen or ["currency"]


def _clamp_anchor(new_divine: float, prev_divine: float | None, cap: float = 3.0) -> float:
    if prev_divine and prev_divine > 0:
        ratio = new_divine / prev_divine
        if ratio > cap or ratio < 1.0 / cap:
            return prev_divine            # implausible jump -> keep previous
    return new_divine


async def poll_once(store, client, cfg: DetectConfig, now_ts: int, breaker, notify) -> int:
    """Run one poll cycle against poe2scout.

    The poe2scout currency response carries NO per-snapshot timestamp, so the bot's own
    fetch time (`now_ts`) is used as the observation `src_ts`. The league's `DivinePrice`
    (Exalted per Divine) is the anchor. Each poll is processed (there is no server epoch
    to dedup on); the `(item_id, src_ts)` primary key still prevents double-insert within
    a single poll.
    """
    league = await store.get_setting("league")
    if not league:
        return 0
    # League meta drives the anchor that normalizes EVERY category into exalt, so a meta failure
    # is systemic — handle it before the per-category loop and bail.
    try:
        meta = await client.get_league_meta(league)
    except Exception:
        if breaker.record_failure():
            await notify({"health": "source_down"})
        return -1
    raw_divine = float(meta["DivinePrice"]) if meta and meta.get("DivinePrice") else 1.0
    raw_chaos = float(meta["ChaosDivinePrice"]) if meta and meta.get("ChaosDivinePrice") else 1.0
    prev_div = await store.get_setting("anchor_divine")
    divine = _clamp_anchor(raw_divine, float(prev_div) if prev_div else None)
    anchor = Anchor(divine_exalt=divine, chaos_divine=raw_chaos)
    await store.set_setting("anchor_divine", str(divine))
    await store.set_setting("anchor_chaos_divine", str(raw_chaos))
    started = await store.get_league_started_at(league)
    if started == 0:
        # bootstrap once (persisted) so the early-league mute has a real anchor;
        # poe2scout exposes no real league start date, so first-poll time is the Phase-1 proxy
        await store.upsert_league(league, league, league, now_ts, anchor.divine_exalt, anchor.chaos_divine)
        await store.set_active_league(league)
        started = now_ts
    # Fetch each configured category; one bad category is skipped (logged), not fatal. The
    # breaker only trips when EVERY category fetch fails (a systemic source outage).
    categories = _scanned_categories(await store.get_setting("categories"))
    obs = []
    attempted = 0       # categories known to the registry (a fetch was tried)
    succeeded = 0       # categories whose fetch+normalize succeeded
    for cat in categories:
        fam = category_family(cat)
        if fam is None:
            log.warning("unknown category %s — skipping", cat)
            continue
        attempted += 1
        try:
            if fam == "uniques":
                raw = await client.get_uniques_overview(league, cat)
                items = normalize_uniques(raw, league, anchor, now_ts, cat)
            else:
                raw = await client.get_currency_overview(league, cat)
                items = normalize_currency(raw, league, anchor, now_ts, cat)
        except Exception as e:
            log.warning("category %s fetch failed: %s", cat, e)
            continue
        succeeded += 1
        obs.extend(items)
    if attempted == 0:
        return 0        # only unknown/typo'd categories configured — a config error, not source-down
    if succeeded == 0:
        if breaker.record_failure():
            await notify({"health": "source_down"})
        return -1
    # Per-category spike floors from the thr:<cat> settings (unset categories use the default).
    category_floors: dict[str, float] = {}
    for cat in categories:
        v = await store.get_setting(f"thr:{cat}")
        if v:
            category_floors[cat] = float(v)
    kept, overflow = await detect(store, obs, anchor, started, now_ts, cfg, category_floors)
    for ev in kept:
        await notify(ev)
    if overflow > 0:
        await notify({"overflow": overflow})
    await store.set_setting("last_poll_ts", str(now_ts))
    breaker.record_success()
    return len(kept)
