import pytest

from reliability import CircuitBreaker, CircuitOpenError


def test_circuit_breaker_opens_after_failures():
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_seconds=60)
    breaker.failure()
    breaker.failure()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_circuit_breaker_recovers_after_success():
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_seconds=60)
    breaker.failure()
    breaker.success()
    breaker.before_call()
