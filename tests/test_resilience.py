from datetime import datetime, timedelta, timezone
import pytest

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.errors.classifier import ErrorClassifier
from lamaru.resilience.circuit_breaker import CircuitBreaker, CircuitState
from lamaru.resilience.cooldown import CooldownState, CooldownTracker
from lamaru.resilience.retry import RetryPolicy, execute_with_retry


# ==========================================
# 1. RETRY TESTS
# ==========================================

def test_retry_on_429_rate_limit_recovers():
    """Verify operation retries on 429 RATE_LIMIT and succeeds on subsequent attempt."""
    attempts = 0
    delays_recorded = []

    async def _op():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise LamaruError("Rate limit reached", category=ErrorCategory.RATE_LIMIT)
        return "success_after_rate_limit"

    async def _mock_sleep(seconds: float):
        delays_recorded.append(seconds)

    policy = RetryPolicy(max_attempts=3, initial_backoff_seconds=0.1, jitter=False)

    import asyncio
    result = asyncio.run(
        execute_with_retry(_op, retry_policy=policy, sleep_func=_mock_sleep)
    )

    assert result == "success_after_rate_limit"
    assert attempts == 3
    assert len(delays_recorded) == 2
    assert delays_recorded[0] == 0.1
    assert delays_recorded[1] == 0.2


def test_retry_on_500_temporary_server_error():
    """Verify operation retries on 500 TEMPORARY_SERVER_ERROR."""
    attempts = 0

    async def _op():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LamaruError("Internal Server Error", category=ErrorCategory.TEMPORARY_SERVER_ERROR)
        return "recovered"

    async def _mock_sleep(seconds: float):
        pass

    policy = RetryPolicy(max_attempts=2, initial_backoff_seconds=0.05, jitter=False)

    import asyncio
    result = asyncio.run(
        execute_with_retry(_op, retry_policy=policy, sleep_func=_mock_sleep)
    )

    assert result == "recovered"
    assert attempts == 2


def test_retry_on_network_failure():
    """Verify operation retries on network exception (e.g. ConnectionResetError)."""
    attempts = 0

    async def _op():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("Connection dropped by peer")
        return "network_recovered"

    async def _mock_sleep(seconds: float):
        pass

    policy = RetryPolicy(max_attempts=2, initial_backoff_seconds=0.01)

    import asyncio
    result = asyncio.run(
        execute_with_retry(_op, retry_policy=policy, sleep_func=_mock_sleep)
    )

    assert result == "network_recovered"
    assert attempts == 2


def test_no_retry_on_permanent_error():
    """Verify permanent errors (AUTH_ERROR, INVALID_REQUEST, SAFETY_BLOCK) abort on attempt 1."""
    for cat in [ErrorCategory.AUTH_ERROR, ErrorCategory.INVALID_REQUEST, ErrorCategory.SAFETY_BLOCK]:
        attempts = 0

        async def _op():
            nonlocal attempts
            attempts += 1
            raise LamaruError(f"Permanent failure: {cat.value}", category=cat)

        async def _mock_sleep(seconds: float):
            pass

        policy = RetryPolicy(max_attempts=5)

        import asyncio
        with pytest.raises(LamaruError) as exc_info:
            asyncio.run(execute_with_retry(_op, retry_policy=policy, sleep_func=_mock_sleep))

        assert exc_info.value.category == cat
        assert attempts == 1, f"Expected exactly 1 attempt for {cat.value}, got {attempts}"


def test_retry_exhausted_raises_final_exception():
    """Verify when max_attempts is exceeded, the error is re-raised."""
    attempts = 0

    async def _op():
        nonlocal attempts
        attempts += 1
        raise LamaruError("Transient error", category=ErrorCategory.TEMPORARY_SERVER_ERROR)

    async def _mock_sleep(seconds: float):
        pass

    policy = RetryPolicy(max_attempts=3, initial_backoff_seconds=0.01)

    import asyncio
    with pytest.raises(LamaruError):
        asyncio.run(execute_with_retry(_op, retry_policy=policy, sleep_func=_mock_sleep))

    assert attempts == 3


def test_exponential_backoff_and_jitter():
    policy = RetryPolicy(
        initial_backoff_seconds=1.0,
        backoff_multiplier=2.0,
        max_backoff_seconds=10.0,
        jitter=False,
    )
    assert policy.calculate_backoff(1) == 1.0
    assert policy.calculate_backoff(2) == 2.0
    assert policy.calculate_backoff(3) == 4.0
    assert policy.calculate_backoff(4) == 8.0
    assert policy.calculate_backoff(5) == 10.0  # Capped at max_backoff_seconds

    # With jitter
    policy_jitter = RetryPolicy(
        initial_backoff_seconds=1.0,
        backoff_multiplier=2.0,
        jitter=True,
        jitter_min=0.9,
        jitter_max=1.1,
    )
    val = policy_jitter.calculate_backoff(2)
    assert 1.8 <= val <= 2.2


# ==========================================
# 2. COOLDOWN LIFECYCLE TESTS
# ==========================================

def test_cooldown_lifecycle_transitions():
    """Verify HEALTHY -> COOLDOWN -> PROBE -> HEALTHY lifecycle and candidate availability."""
    tracker = CooldownTracker()
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Initial: HEALTHY
    assert tracker.get_state("key-01", now=now) == CooldownState.HEALTHY
    assert tracker.is_available("key-01", now=now) is True

    # 2. Mark cooldown for 60 seconds
    tracker.mark_cooldown("key-01", duration_seconds=60.0, now=now)
    assert tracker.get_state("key-01", now=now) == CooldownState.COOLDOWN
    # Candidate in cooldown must NEVER be available
    assert tracker.is_available("key-01", now=now) is False

    # 3. Time advanced 30s: still in COOLDOWN
    t_30s = now + timedelta(seconds=30)
    assert tracker.get_state("key-01", now=t_30s) == CooldownState.COOLDOWN
    assert tracker.is_available("key-01", now=t_30s) is False

    # 4. Time advanced 61s: enters PROBE
    t_61s = now + timedelta(seconds=61)
    assert tracker.get_state("key-01", now=t_61s) == CooldownState.PROBE
    assert tracker.is_available("key-01", now=t_61s) is True

    # Exactly one probe request allowed
    assert tracker.acquire_probe("key-01", now=t_61s) is True
    # While probe is in flight, other requests cannot use it
    assert tracker.acquire_probe("key-01", now=t_61s) is False
    assert tracker.is_available("key-01", now=t_61s) is False

    # 5. Probe succeeds -> returns to HEALTHY
    tracker.mark_healthy("key-01")
    assert tracker.get_state("key-01", now=t_61s) == CooldownState.HEALTHY
    assert tracker.is_available("key-01", now=t_61s) is True


# ==========================================
# 3. CIRCUIT BREAKER TESTS
# ==========================================

def test_circuit_breaker_closed_to_open_recovery():
    """Verify CLOSED -> OPEN -> HALF_OPEN -> CLOSED state transitions."""
    cb = CircuitBreaker(target_id="gemini-model", failure_threshold=3, cooldown_seconds=30.0)
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)

    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute(now=now) is True

    # 2 failures do not trip
    cb.record_failure(now=now)
    cb.record_failure(now=now)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute(now=now) is True

    # 3rd failure trips to OPEN
    cb.record_failure(now=now)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute(now=now) is False

    # While in cooldown, execution rejected
    t_10s = now + timedelta(seconds=10)
    assert cb.can_execute(now=t_10s) is False

    # Cooldown expires at 30s -> transitions to HALF_OPEN
    t_31s = now + timedelta(seconds=31)
    assert cb.can_execute(now=t_31s) is True
    assert cb.state == CircuitState.HALF_OPEN

    # Acquire probe
    assert cb.acquire_execution(now=t_31s) is True
    # Cannot send second probe while probe in flight
    assert cb.can_execute(now=t_31s) is False

    # Probe succeeds -> transitions to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute(now=t_31s) is True
    assert cb.consecutive_failures == 0


def test_circuit_breaker_half_open_failure_re_trips_to_open():
    """Verify failure during HALF_OPEN immediately trips back to OPEN."""
    cb = CircuitBreaker(target_id="gemini-model", failure_threshold=2, cooldown_seconds=10.0)
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)

    # Trip to OPEN
    cb.record_failure(now=now)
    cb.record_failure(now=now)
    assert cb.state == CircuitState.OPEN

    # Advance to HALF_OPEN
    t_11s = now + timedelta(seconds=11)
    assert cb.acquire_execution(now=t_11s) is True
    assert cb.state == CircuitState.HALF_OPEN

    # Probe fails -> immediately trips back to OPEN
    cb.record_failure(now=t_11s)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute(now=t_11s) is False
