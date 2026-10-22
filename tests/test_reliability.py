import pytest

from reliability import CircuitBreaker, CircuitOpenError
from observability import extract_usage


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


def test_extract_usage_supports_langchain_metadata():
    class Message:
        usage_metadata = {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}

    assert extract_usage(Message()) == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
