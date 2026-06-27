import math
from poe2bot.detector.engine import DetectConfig, evaluate_price
from poe2bot.models import Observation, LiquidityTier

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
