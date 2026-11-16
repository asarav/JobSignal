"""Circuit breaker unit tests."""

from app.plugins.isolation import CircuitBreaker, CircuitState


def test_breaker_closes_after_successes():
    b = CircuitBreaker(consecutive_failures=3)
    assert b.state == CircuitState.CLOSED
    for _ in range(5):
        b.record_success()
    assert b.state == CircuitState.CLOSED


def test_breaker_opens_after_consecutive_failures():
    b = CircuitBreaker(consecutive_failures=3)
    for _ in range(3):
        b.record_failure()
    assert b.state == CircuitState.OPEN


def test_breaker_allows_half_open_probe_after_cooldown():
    from datetime import timedelta

    b = CircuitBreaker(consecutive_failures=2, cooldown=timedelta(seconds=0))
    for _ in range(2):
        b.record_failure()
    assert b.state == CircuitState.OPEN
    assert b.allow() is True  # cooldown zero => half-open probe allowed


def test_breaker_health_roundtrip():
    b = CircuitBreaker(consecutive_failures=3)
    for _ in range(3):
        b.record_failure()
    health = b.to_health()
    b2 = CircuitBreaker.from_health(health)
    assert b2.state == b.state