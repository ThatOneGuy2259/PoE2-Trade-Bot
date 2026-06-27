from __future__ import annotations
from .models import Anchor
from .sources.normalize import normalize_currency
from .detector.engine import detect, DetectConfig


def extract_src_ts(raw: dict) -> int:
    return int(raw.get("epoch", 0))


def extract_anchor(raw: dict) -> Anchor:
    for it in raw.get("items", []):
        if it.get("apiId") == "divine" and it.get("currentPrice"):
            # currentPrice is in Exalted-equiv units in this overview
            return Anchor(divine_exalt=float(it["currentPrice"]) or 1.0, chaos_divine=1.0)
    return Anchor(divine_exalt=1.0, chaos_divine=1.0)


def _clamp_anchor(new_divine: float, prev_divine: float | None, cap: float = 3.0) -> float:
    if prev_divine and prev_divine > 0:
        ratio = new_divine / prev_divine
        if ratio > cap or ratio < 1.0 / cap:
            return prev_divine            # implausible jump -> keep previous
    return new_divine


async def poll_once(store, client, cfg: DetectConfig, now_ts: int, breaker, notify) -> int:
    league = await store.get_setting("league")
    if not league:
        return 0
    try:
        raw = await client.get_currency_overview(league)
    except Exception:
        if breaker.record_failure():
            await notify({"health": "source_down"})
        return -1
    src_ts = extract_src_ts(raw)
    last = await store.get_setting("last_poll_ts")
    if last is not None and int(last) == src_ts:
        breaker.record_success()
        return 0
    raw_anchor = extract_anchor(raw)
    prev_div = await store.get_setting("anchor_divine")
    divine = _clamp_anchor(raw_anchor.divine_exalt, float(prev_div) if prev_div else None)
    anchor = Anchor(divine_exalt=divine, chaos_divine=raw_anchor.chaos_divine)
    await store.set_setting("anchor_divine", str(divine))
    started = await store.get_league_started_at(league)
    if started == 0:
        # bootstrap once (persisted) so the early-league mute has a real anchor;
        # poe2scout exposes no real league start date, so first-poll time is the Phase-1 proxy
        await store.upsert_league(league, league, league, now_ts, anchor.divine_exalt, anchor.chaos_divine)
        await store.set_active_league(league)
        started = now_ts
    league_started_at = started
    obs = normalize_currency(raw, league, anchor, src_ts)
    kept, overflow = await detect(store, obs, anchor, league_started_at, now_ts, cfg)
    for ev in kept:
        await notify(ev)
    if overflow > 0:
        await notify({"overflow": overflow})
    await store.set_setting("last_poll_ts", str(src_ts))
    breaker.record_success()
    return len(kept)
