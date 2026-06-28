import math
import pytest
from poe2bot.detector.engine import DetectConfig, evaluate_price, evaluate_demand, detect
from poe2bot.store import Store
from poe2bot.models import Observation, AlertEvent, Anchor, LiquidityTier

# legacy tests use a baseline of 1.0 ex and assert single-call 2-of-3/fast-path behavior, so they
# run with use_cusum=False (the rollback path) and the junk-price gate disabled.
def _cfg(**kw):
    return DetectConfig(min_alert_price_exalt=0, use_cusum=False, **kw)

def _obs(price, tier=LiquidityTier.HIGH, src_ts=1000):
    return Observation(item_id="divine", league_id="L", src_ts=src_ts, wall_ts=src_ts,
                       name="Divine Orb", category="currency", is_currency_pair=True,
                       log_price=math.log(price), price_exalt=price, volume=1500.0,
                       vol_daily=None, stock=None, doi=None, liq_tier=tier,
                       trade_id="divine", valid=True)

def test_jump_fires_above_floor():
    cfg = _cfg()
    base = [math.log(1.0)] * 20
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=base,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg)
    assert v.event is not None and v.event.cls == "JUMP" and v.event.direction == "up"
    assert v.new_mu_frozen == math.log(1.30)
    assert v.fast_path is False        # +30% clears the 15% floor but is below the 0.40 fast-path

def test_fast_path_flagged_above_040():
    cfg = _cfg()
    v = evaluate_price(_obs(1.6), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg)
    assert v.event is not None and v.fast_path is True    # log(1.6)=0.47 >= 0.40

def test_early_league_mutes_crash():
    cfg = _cfg()
    v = evaluate_price(_obs(0.70), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg, early_league=True)
    assert v.event is None and v.reason == "early_league_mute"

def test_early_league_does_not_mute_jump():
    cfg = _cfg()
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg, early_league=True)
    assert v.event is not None and v.event.cls == "JUMP"

def test_small_move_no_fire():
    cfg = _cfg()
    base = [math.log(1.0)] * 20
    v = evaluate_price(_obs(1.05), mu_frozen=math.log(1.0), baseline_logs=base,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg)
    assert v.event is None

def test_crash_fires_below_floor():
    cfg = _cfg()
    v = evaluate_price(_obs(0.70), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg)
    assert v.event is not None and v.event.cls == "CRASH" and v.event.direction == "down"

def test_cheap_item_uses_higher_floor():
    cfg = _cfg()
    # price 0.10 (<2 cheap) moved +18% -> below 25% cheap floor, no fire
    v = evaluate_price(_obs(0.118), mu_frozen=math.log(0.10), baseline_logs=[math.log(0.10)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg)
    assert v.event is None

def test_cooldown_suppresses_same_direction():
    cfg = _cfg(cooldown_s=21600)
    v = evaluate_price(_obs(1.30), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=9_000, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg)
    assert v.event is None and v.reason == "cooldown"


def test_category_floor_overrides_base():
    cfg = _cfg()
    base = [math.log(1.0)] * 20
    # +30% clears the default 0.15 floor and fires...
    assert evaluate_price(_obs(1.30), math.log(1.0), base, 0, 0, 10_000,
                          Anchor(250.0, 1.0), cfg).event is not None
    # ...but a per-category floor of 0.40 for "currency" raises the bar above +30% -> no fire
    v = evaluate_price(_obs(1.30), math.log(1.0), base, 0, 0, 10_000, Anchor(250.0, 1.0), cfg,
                       category_floors={"currency": 0.40})
    assert v.event is None


def test_category_floor_cannot_weaken_low_liq_guard():
    cfg = _cfg()
    base = [math.log(1.0)] * 20
    # a LOOSE category floor (0.05) must not lower the LOW-liquidity guard (0.40):
    # +30% on a LOW item still does not fire.
    v = evaluate_price(_obs(1.30, tier=LiquidityTier.LOW), math.log(1.0), base, 0, 0, 10_000,
                       Anchor(250.0, 1.0), cfg, category_floors={"currency": 0.05})
    assert v.event is None


# ---------------------------------------------------------------------------
# Task 11: evaluate_demand + detect() orchestration
# ---------------------------------------------------------------------------

def _cur(price, vol, src_ts, item="divine", tier=LiquidityTier.HIGH):
    return Observation(item_id=item, league_id="L", src_ts=src_ts, wall_ts=src_ts,
                       name=item, category="currency", is_currency_pair=True,
                       log_price=math.log(price), price_exalt=price, volume=vol, vol_daily=None,
                       stock=None, doi=None, liq_tier=tier, trade_id=item, valid=True)


def test_demand_collapse_fires():
    cfg = _cfg()
    obs = _cur(1.0, vol=8000.0, src_ts=5000)       # current daily volume 8000
    baseline = [20000.0] * 20                       # median 20000 -> drop 60% >= 50%
    ev = evaluate_demand(obs, baseline, early_league=False, cfg=cfg)
    assert ev is not None and ev.cls == "DEMAND_COLLAPSE"


def test_demand_collapse_blocked_below_floor():
    cfg = _cfg()                            # demand_min_volume default 5000
    obs = _cur(1.0, vol=1000.0, src_ts=5000)
    baseline = [3000.0] * 20                         # median 3000 < 5000 daily-volume floor
    assert evaluate_demand(obs, baseline, early_league=False, cfg=cfg) is None


def test_demand_collapse_muted_early_league():
    cfg = _cfg()
    obs = _cur(1.0, vol=8000.0, src_ts=5000)
    assert evaluate_demand(obs, [20000.0]*20, early_league=True, cfg=cfg) is None


async def _seed_flat(store, item, base_ts, n=20, price=1.0, vol=1500.0):
    for k in range(n):
        await store.insert_observation(_cur(price, vol, base_ts + k, item=item))
    await store.update_detector_state(item, mu_frozen=math.log(price), n_obs=n)


async def test_fast_path_fires_immediately_and_topk_caps(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = _cfg(top_k=2)
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


async def test_per_category_cap_is_independent(tmp_path):
    """top_k caps each category SEPARATELY, so a volatile category can't starve another."""
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = _cfg(top_k=2)
    def cat_obs(item, price, category, src_ts):
        return Observation(item_id=item, league_id="L", src_ts=src_ts, wall_ts=src_ts,
                           name=item, category=category, is_currency_pair=True,
                           log_price=math.log(price), price_exalt=price, volume=1500.0,
                           vol_daily=None, stock=None, doi=None, liq_tier=LiquidityTier.HIGH,
                           trade_id=item, valid=True)
    obss = []
    for cat in ("currency", "fragments"):
        for i in range(3):                                   # 3 fast-path jumps per category
            item = f"{cat}-{i}"
            await _seed_flat(s, item, base_ts=100)
            obss.append(cat_obs(item, 1.5 + i * 0.2, cat, src_ts=100_000 + i))
    kept, overflow = await detect(s, obss, Anchor(250.0, 1.0), league_started_at=0,
                                  now_ts=100_010, cfg=cfg, category_floors=None)
    assert len(kept) == 4 and overflow == 2                  # 2 kept per category, 1 over each
    from collections import Counter
    by_cat = Counter(ev.item_id.split("-")[0] for ev in kept)
    assert by_cat["currency"] == 2 and by_cat["fragments"] == 2
    await s.close()


async def test_two_of_three_persistence(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = _cfg()
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
    cfg = _cfg()  # cooldown_s=21600

    # Seed 20 obs at high daily volume (base_ts=280_000) within the 48h volume window of our
    # test obs (obs1.src_ts=299_900 → window since 299_900-172800=127_100; 280_000 >= 127_100 ✓)
    await _seed_flat(s, "divine", base_ts=280_000, n=20, price=1.0, vol=20000.0)

    # Poll 1: volume collapses from ~20000 to 8000 (>50% drop) -> DEMAND_COLLAPSE fires
    # now_ts_1=300_000 > 172_800 with league_started_at=0 -> not early-league ✓
    # freshness: now_ts_1 - src_ts = 300_000 - 299_900 = 100 <= 10800 ✓
    obs1 = _cur(1.0, vol=8000.0, src_ts=299_900)
    now_ts_1 = 300_000
    k1, ov1 = await detect(s, [obs1], Anchor(250.0, 1.0), league_started_at=0,
                           now_ts=now_ts_1, cfg=cfg)
    assert len(k1) == 1 and k1[0].cls == "DEMAND_COLLAPSE", f"expected DEMAND_COLLAPSE, got {k1}"

    # Poll 2: collapse persists but now_ts_2 is within cooldown_s -> suppressed
    obs2 = _cur(1.0, vol=8000.0, src_ts=300_000)  # fresh src_ts, same low volume
    now_ts_2 = 300_110  # only 110s after now_ts_1, well within 21600s cooldown
    k2, ov2 = await detect(s, [obs2], Anchor(250.0, 1.0), league_started_at=0,
                           now_ts=now_ts_2, cfg=cfg)
    assert k2 == [], f"expected suppressed demand, got {k2}"

    await s.close()


async def test_low_liquidity_needs_confirmation_and_flags(tmp_path):
    """A LOW-liquidity item must clear 2-of-3 even on a big move (no immediate fast-path),
    and its alert is tagged low_confidence."""
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = _cfg()
    await _seed_flat(s, "rare", base_ts=99_980, vol=800.0)     # daily vol 800 -> LOW tier
    def low_obs(src_ts):
        return _cur(1.6, 800.0, src_ts=src_ts, item="rare", tier=LiquidityTier.LOW)  # +60% move
    k1, _ = await detect(s, [low_obs(100_001)], Anchor(250.0, 1.0),
                         league_started_at=0, now_ts=100_010, cfg=cfg)
    assert k1 == []                                            # +60% on a LOW item does NOT fire immediately
    k2, _ = await detect(s, [low_obs(100_002)], Anchor(250.0, 1.0),
                         league_started_at=0, now_ts=101_810, cfg=cfg)
    assert len(k2) == 1 and k2[0].cls == "JUMP" and k2[0].low_confidence is True
    await s.close()


async def test_statistical_path_blocked_during_warmup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = _cfg()                                # min_samples 12
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


# ---------------------------------------------------------------------------
# Phase 2B: junk-price gate (default DetectConfig, min_alert_price_exalt=1.0)
# ---------------------------------------------------------------------------

def test_junk_gate_suppresses_floor_sitter():
    # baseline at the 1-ex display floor + a 1->2 ex (+100%) tick: clears the floor but is junk
    v = evaluate_price(_obs(2.0), mu_frozen=math.log(1.0), baseline_logs=[math.log(1.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=DetectConfig(use_cusum=False))
    assert v.event is None and v.reason == "below_min_price"


def test_junk_gate_allows_valuable_item():
    # baseline 5 ex (above the floor) + 30%: a real signal, not gated
    v = evaluate_price(_obs(6.5), mu_frozen=math.log(5.0), baseline_logs=[math.log(5.0)]*20,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=DetectConfig(use_cusum=False))
    assert v.event is not None and v.event.cls == "JUMP"


def test_junk_gate_blocks_demand_on_floor_item():
    obs = _cur(1.0, vol=8000.0, src_ts=5000)            # priced at the floor
    assert evaluate_demand(obs, [20000.0]*20, early_league=False, cfg=DetectConfig(use_cusum=False)) is None


def test_junk_gate_allows_demand_on_valuable_item():
    obs = _cur(5.0, vol=8000.0, src_ts=5000)            # 5 ex, real item
    ev = evaluate_demand(obs, [20000.0]*20, early_league=False, cfg=DetectConfig(use_cusum=False))
    assert ev is not None and ev.cls == "DEMAND_COLLAPSE"


async def test_junk_gate_records_suppression_in_detect(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = DetectConfig(use_cusum=False)                                # gate ON (default 1.0)
    await _seed_flat(s, "junk", base_ts=99_980, price=1.0)
    k, o = await detect(s, [_cur(2.0, 1500.0, src_ts=100_001, item="junk")], Anchor(250.0, 1.0),
                        league_started_at=0, now_ts=100_010, cfg=cfg)
    assert k == []                                      # floor-sitter mover suppressed, not fired
    row = await (await s._db.execute(
        "SELECT suppressed_reason FROM alert_log WHERE fired=0 ORDER BY alert_id DESC LIMIT 1")).fetchone()
    assert row["suppressed_reason"] == "below_min_price"
    await s.close()


# ---------------------------------------------------------------------------
# Phase 3: self-normalizing CUSUM (default use_cusum=True)
# ---------------------------------------------------------------------------

def _ccfg(**kw):
    return DetectConfig(min_alert_price_exalt=0, **kw)        # CUSUM on (default), junk gate off


def test_cusum_cold_start_no_raise_no_fire():
    # empty baseline must not call mad([]) and must not fire (still warming)
    v = evaluate_price(_obs(5.0), mu_frozen=None, baseline_logs=[], last_fire_up_ts=0,
                       last_fire_dn_ts=0, now_ts=10_000, anchor=Anchor(250.0, 1.0), cfg=DetectConfig())
    assert v.event is None and v.cusum_pos == 0.0 and v.cusum_neg == 0.0


def test_cusum_accumulates_over_polls_then_fires():
    cfg = _ccfg()
    base = [math.log(5.0)] * 20                              # constant -> scale floors to 0.05
    def step(cp):
        return evaluate_price(_obs(5.75), mu_frozen=math.log(5.0), baseline_logs=base,
                              last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                              anchor=Anchor(250.0, 1.0), cfg=cfg, cusum_pos=cp)
    v1 = step(0.0); assert v1.event is None and 2.2 < v1.cusum_pos < 2.4    # +15% accrues, not yet h
    v2 = step(v1.cusum_pos); assert v2.event is None and v2.cusum_pos > 4.5
    v3 = step(v2.cusum_pos)
    assert v3.event is not None and v3.event.cls == "JUMP"   # crosses h=5 -> fires
    assert v3.cusum_pos == 0.0                               # reset on fire


def test_cusum_transient_revert_decays_no_fire():
    cfg = _ccfg()
    base = [math.log(5.0)] * 20
    v1 = evaluate_price(_obs(5.75), math.log(5.0), base, 0, 0, 10_000, Anchor(250.0, 1.0), cfg, cusum_pos=0.0)
    v2 = evaluate_price(_obs(5.0), math.log(5.0), base, 0, 0, 10_000, Anchor(250.0, 1.0), cfg,
                        cusum_pos=v1.cusum_pos)              # price reverts to baseline
    assert v2.event is None and v2.cusum_pos < v1.cusum_pos  # s_pos decays by k


def test_cusum_sign_agreement():
    cfg = _ccfg()
    base = [math.log(5.0)] * 20
    # s_pos already past h, but the CURRENT move is down -> no up-fire, and the down side isn't at h
    v = evaluate_price(_obs(4.0), math.log(5.0), base, 0, 0, 10_000, Anchor(250.0, 1.0), cfg,
                       cusum_pos=10.0, cusum_neg=0.0)
    assert v.event is None


def test_cusum_floor_gate_blocks_subfloor_hit():
    cfg = _ccfg()
    base = [math.log(5.0)] * 20
    # CUSUM well past h, but the move (+8%) is below the 15% floor -> not a fire candidate
    v = evaluate_price(_obs(5.4), math.log(5.0), base, 0, 0, 10_000, Anchor(250.0, 1.0), cfg, cusum_pos=10.0)
    assert v.event is None


def test_cusum_fast_path_fires_during_warmup():
    cfg = _ccfg()
    # only 3 samples (warmup) but a +60% move is fast-path -> fires immediately, bypassing min_samples
    v = evaluate_price(_obs(8.0), mu_frozen=None, baseline_logs=[math.log(5.0)] * 3,
                       last_fire_up_ts=0, last_fire_dn_ts=0, now_ts=10_000,
                       anchor=Anchor(250.0, 1.0), cfg=cfg)
    assert v.event is not None and v.fast_path is True


def test_cusum_scale_is_volatility_adaptive():
    cfg = _ccfg()
    c = math.log(5.0)
    volatile = [c - 0.0674, c + 0.0674] * 10                 # mad=0.0674 -> scale≈0.10
    flat = [c] * 20                                          # mad=0 -> scale floors to 0.05
    def s_after(base):
        return evaluate_price(_obs(5.75), mu_frozen=c, baseline_logs=base, last_fire_up_ts=0,
                              last_fire_dn_ts=0, now_ts=10_000, anchor=Anchor(250.0, 1.0),
                              cfg=cfg, cusum_pos=0.0).cusum_pos
    # the same +15% move accrues LESS on a volatile item (bigger scale) -> needs more polls to fire
    assert s_after(volatile) < s_after(flat)


def test_cusum_junk_gate_only_on_fire_candidate():
    cfg = DetectConfig()                                     # CUSUM on, junk gate on (min=1.0)
    base = [math.log(1.0)] * 20
    # floor-sitter with a big accumulated CUSUM and a +100% move -> would-be fire is junk-gated
    v = evaluate_price(_obs(2.0), mu_frozen=math.log(1.0), baseline_logs=base, last_fire_up_ts=0,
                       last_fire_dn_ts=0, now_ts=10_000, anchor=Anchor(250.0, 1.0), cfg=cfg, cusum_pos=10.0)
    assert v.event is None and v.reason == "below_min_price"


async def test_cusum_detect_fires_and_resets_state(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = _ccfg()
    await _seed_flat(s, "divine", base_ts=99_980, price=5.0)   # mu_frozen=log(5.0), 20 in-window samples
    fired = None
    for i in range(5):
        k, _o = await detect(s, [_cur(5.75, 1500.0, src_ts=100_001 + i * 100, item="divine")],
                             Anchor(250.0, 1.0), league_started_at=0, now_ts=100_010 + i * 100, cfg=cfg)
        if k:
            fired = k[0]; break
    assert fired is not None and fired.cls == "JUMP"           # sustained +15% fires within a few polls
    assert (await s.get_detector_state("divine"))["cusum_pos"] == 0.0   # reset on fire
    await s.close()


async def test_cusum_freezes_reference_on_warmup(tmp_path):
    s = await Store.open(str(tmp_path / "t.db"))
    cfg = _ccfg()
    for k in range(12):                                        # 12 prior samples, NO pre-set mu_frozen
        await s.insert_observation(_cur(5.0, 1500.0, 99_980 + k, item="x"))
    await detect(s, [_cur(5.0, 1500.0, src_ts=100_001, item="x")], Anchor(250.0, 1.0),
                 league_started_at=0, now_ts=100_010, cfg=cfg)
    st = await s.get_detector_state("x")
    assert st["mu_frozen"] is not None                         # reference frozen at warmup, before any fire
    await s.close()
