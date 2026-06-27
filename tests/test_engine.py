import math
import pytest
from poe2bot.detector.engine import DetectConfig, evaluate_price, evaluate_demand, detect
from poe2bot.store import Store
from poe2bot.models import Observation, AlertEvent, Anchor, LiquidityTier

def _obs(price, tier=LiquidityTier.HIGH, src_ts=1000):
    return Observation(item_id="divine", league_id="L", src_ts=src_ts, wall_ts=src_ts,
                       name="Divine Orb", category="currency", is_currency_pair=True,
                       log_price=math.log(price), price_exalt=price, volume=1500.0,
                       vol_daily=None, stock=None, doi=None, liq_tier=tier,
                       trade_id="divine", valid=True)

def test_jump_fires_above_floor():
    cfg = DetectConfig()
    base = [math.log(1.0)] * 20
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=base,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is not None and v.event.cls == "JUMP" and v.event.direction == "up"
    assert v.new_mu_frozen == math.log(1.30)
    assert v.fast_path is False        # +30% clears the 15% floor but is below the 0.40 fast-path

def test_fast_path_flagged_above_040():
    cfg = DetectConfig()
    v = evaluate_price(_obs(1.6), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is not None and v.fast_path is True    # log(1.6)=0.47 >= 0.40

def test_early_league_mutes_crash():
    cfg = DetectConfig()
    v = evaluate_price(_obs(0.70), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg, early_league=True)
    assert v.event is None and v.reason == "early_league_mute"

def test_early_league_does_not_mute_jump():
    cfg = DetectConfig()
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg, early_league=True)
    assert v.event is not None and v.event.cls == "JUMP"

def test_small_move_no_fire():
    cfg = DetectConfig()
    base = [math.log(1.0)] * 20
    v = evaluate_price(_obs(1.05), mu_frozen=math.log(1.0), baseline_logs=base,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is None

def test_crash_fires_below_floor():
    cfg = DetectConfig()
    v = evaluate_price(_obs(0.70), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is not None and v.event.cls == "CRASH" and v.event.direction == "down"

def test_cheap_item_uses_higher_floor():
    cfg = DetectConfig()
    # price 0.10 (<2 cheap) moved +18% -> below 25% cheap floor, no fire
    v = evaluate_price(_obs(0.118), mu_frozen=math.log(0.10), baseline_logs=[math.log(0.10)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is None

def test_cooldown_suppresses_same_direction():
    cfg = DetectConfig(cooldown_s=21600)
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=9_000, last_fire_dn_ts=0, now_ts=10_000,
                       divine_exalt=250.0, cfg=cfg)
    assert v.event is None and v.reason == "cooldown"


# ---------------------------------------------------------------------------
# Task 11: evaluate_demand + detect() orchestration
# ---------------------------------------------------------------------------

def _cur(price, vol, src_ts, item="divine", tier=LiquidityTier.HIGH):
    return Observation(item_id=item, league_id="L", src_ts=src_ts, wall_ts=src_ts,
                       name=item, category="currency", is_currency_pair=True,
                       log_price=math.log(price), price_exalt=price, volume=vol, vol_daily=None,
                       stock=None, doi=None, liq_tier=tier, trade_id=item, valid=True)


def test_demand_collapse_fires():
    cfg = DetectConfig()
    obs = _cur(1.0, vol=200.0, src_ts=5000)       # current flow 200
    baseline = [500.0] * 20                        # median 500 -> drop 60% >= 50%
    ev = evaluate_demand(obs, baseline, early_league=False, cfg=cfg)
    assert ev is not None and ev.cls == "DEMAND_COLLAPSE"


def test_demand_collapse_blocked_below_floor():
    cfg = DetectConfig(demand_min_trades_day=10.0)
    obs = _cur(1.0, vol=2.0, src_ts=5000)
    baseline = [5.0] * 20                           # median 5 < 10 trades/day floor
    assert evaluate_demand(obs, baseline, early_league=False, cfg=cfg) is None


def test_demand_collapse_muted_early_league():
    cfg = DetectConfig()
    obs = _cur(1.0, vol=200.0, src_ts=5000)
    assert evaluate_demand(obs, [500.0]*20, early_league=True, cfg=cfg) is None


async def _seed_flat(store, item, base_ts, n=20, price=1.0):
    for k in range(n):
        await store.insert_observation(_cur(price, 1500.0, base_ts + k, item=item))
    await store.update_detector_state(item, mu_frozen=math.log(price), n_obs=n)


async def test_fast_path_fires_immediately_and_topk_caps(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig(top_k=2)
    obss = []
    for i, item in enumerate(["a", "b", "c"]):
        await _seed_flat(s, item, base_ts=100)
        # jumps 1.5/1.7/1.9 -> log 0.405/0.531/0.642, all >= 0.40 fast-path
        obss.append(_cur(1.5 + i * 0.2, 1500.0, src_ts=100_000 + i, item=item))
    # now_ts aligned to src_ts so the freshness gate sees the data as current
    kept, overflow = await detect(s, obss, Anchor(250.0, 1.0), league_started_at=0,
                                  now_ts=100_010, cfg=cfg)
    assert len(kept) == 2 and overflow == 1            # all 3 fire, top-2 kept
    assert kept[0].severity >= kept[1].severity        # sorted by severity
    # detector_state re-frozen + alert_log fired rows written
    st = await s.get_detector_state("c")
    assert st["mu_frozen"] is not None
    cur = await s._db.execute("SELECT COUNT(*) c FROM alert_log WHERE fired=1")
    assert (await cur.fetchone())["c"] == 2
    await s.close()


async def test_two_of_three_persistence(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig()
    # seed WITHIN the 24h src_ts window of the confirming obs (100_001 - 86_400 = 13_601),
    # so the statistical path has >=12 in-window samples and 2-of-3 is actually exercised.
    await _seed_flat(s, "divine", base_ts=99_980)
    # +30% -> log 0.262: clears 15% floor, below 0.40 fast-path -> needs 2-of-3
    k1, o1 = await detect(s, [_cur(1.30, 1500.0, src_ts=100_001)], Anchor(250.0, 1.0),
                          league_started_at=0, now_ts=100_010, cfg=cfg)
    assert k1 == []                                    # first sighting: pending=1, no fire
    k2, o2 = await detect(s, [_cur(1.30, 1500.0, src_ts=100_002)], Anchor(250.0, 1.0),
                          league_started_at=0, now_ts=101_810, cfg=cfg)
    assert len(k2) == 1 and k2[0].cls == "JUMP"        # second confirming poll fires
    st = await s.get_detector_state("divine")
    assert st["mu_frozen"] is not None and st["last_fire_up_ts"] == 101_810
    await s.close()


async def test_demand_collapse_cooldown_suppresses_second_fire(tmp_path):
    """DEMAND_COLLAPSE fires on first poll; a second poll within cooldown_s is suppressed."""
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig()  # cooldown_s=21600

    # Seed 20 obs at high volume (base_ts=280_000) within the 48h volume window of our test obs
    # (obs1.src_ts=299_900 → window since 299_900-172800=127_100; 280_000 >= 127_100 ✓)
    await _seed_flat(s, "divine", base_ts=280_000, n=20, price=1.0)

    # Poll 1: volume collapses from ~1500 to 200 (>50% drop) -> DEMAND_COLLAPSE fires
    # now_ts_1=300_000 > 172_800 with league_started_at=0 -> not early-league ✓
    # freshness: now_ts_1 - src_ts = 300_000 - 299_900 = 100 <= 10800 ✓
    obs1 = _cur(1.0, vol=200.0, src_ts=299_900)
    now_ts_1 = 300_000
    k1, ov1 = await detect(s, [obs1], Anchor(250.0, 1.0), league_started_at=0,
                           now_ts=now_ts_1, cfg=cfg)
    assert len(k1) == 1 and k1[0].cls == "DEMAND_COLLAPSE", f"expected DEMAND_COLLAPSE, got {k1}"

    # Poll 2: collapse persists but now_ts_2 is within cooldown_s -> suppressed
    obs2 = _cur(1.0, vol=200.0, src_ts=300_000)  # fresh src_ts, same low volume
    now_ts_2 = 300_110  # only 110s after now_ts_1, well within 21600s cooldown
    k2, ov2 = await detect(s, [obs2], Anchor(250.0, 1.0), league_started_at=0,
                           now_ts=now_ts_2, cfg=cfg)
    assert k2 == [], f"expected suppressed demand, got {k2}"

    await s.close()


async def test_statistical_path_blocked_during_warmup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig()                                # min_samples 12
    # only 3 prior samples -> a non-fast-path +30% move is suppressed as insufficient_samples
    await _seed_flat(s, "divine", base_ts=100, n=3)
    k, o = await detect(s, [_cur(1.30, 1500.0, src_ts=100_010)], Anchor(250.0, 1.0),
                        league_started_at=0, now_ts=100_020, cfg=cfg)
    assert k == []
    # ...but a fast-path move (>=0.40) DOES fire even with only 3 samples
    k2, o2 = await detect(s, [_cur(1.7, 1500.0, src_ts=100_011)], Anchor(250.0, 1.0),
                          league_started_at=0, now_ts=100_030, cfg=cfg)
    assert len(k2) == 1 and k2[0].cls == "JUMP"
    await s.close()
