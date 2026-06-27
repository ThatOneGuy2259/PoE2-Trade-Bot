from poe2bot.health import CircuitBreaker


def test_breaker_trips_once_at_threshold():
    cb = CircuitBreaker(threshold=3)
    assert cb.record_failure() is False
    assert cb.record_failure() is False
    assert cb.record_failure() is True     # trips now
    assert cb.is_open is True
    assert cb.record_failure() is False    # already open, not a new trip


def test_breaker_resets_on_success():
    cb = CircuitBreaker(threshold=2)
    cb.record_failure()
    cb.record_success()
    assert cb.is_open is False
    assert cb.record_failure() is False    # counter reset
