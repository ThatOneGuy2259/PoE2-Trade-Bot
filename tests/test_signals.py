import math
import pytest
from poe2bot.signals import (to_log_price, median, mad, robust_z, pct_from_log,
                             relative_drop, wfs_phase1)

def test_median_and_mad():
    assert median([1, 3, 2]) == 2
    assert mad([1, 1, 1]) == 0.0
    assert mad([1, 2, 3, 4, 5]) == 1.0  # |.-3| = 2,1,0,1,2 -> median 1

def test_median_empty_raises():
    with pytest.raises(ValueError):
        median([])

def test_robust_z_uses_eps_when_mad_zero():
    # mad 0 -> divides by eps -> large but finite, not ZeroDivision
    z = robust_z(2.0, 1.0, 0.0)
    assert math.isfinite(z) and z > 0

def test_pct_from_log_roundtrip():
    assert pct_from_log(math.log(1.2), math.log(1.0)) == pytest.approx(0.2)
    assert pct_from_log(math.log(0.5), math.log(1.0)) == pytest.approx(-0.5)

def test_relative_drop():
    assert relative_drop(50, 100) == pytest.approx(0.5)
    assert relative_drop(100, 100) == pytest.approx(0.0)

def test_wfs_phase1_zero_gate_is_zero():
    assert wfs_phase1(100.0, 0.0, 1.0, 1000.0) == 0.0

def test_wfs_phase1_monotonic_in_price():
    a = wfs_phase1(10.0, 1.0, 1.0, 100.0)
    b = wfs_phase1(20.0, 1.0, 1.0, 100.0)
    assert b > a
