"""Circuit Breaker state machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""

from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Lifecycle states of the Circuit Breaker pattern."""

    CLOSED = "CLOSED"        # Normal operation: traffic flows through
    OPEN = "OPEN"            # Tripped: all traffic rejected immediately
    HALF_OPEN = "HALF_OPEN"  # Testing: trial probe request allowed to verify recovery


class CircuitBreaker:
    """Circuit breaker protecting downstream services and models from cascading failures.

    Lifecycle:
    - CLOSED: Normal operation. Increments failure count on errors. If failures >= threshold -> OPEN.
    - OPEN: All traffic blocked. Stays open for cooldown_seconds. When cooldown expires -> HALF_OPEN.
    - HALF_OPEN: Allows a probe request.
      - Probe succeeds -> CLOSED (healthy recovery).
      - Probe fails -> OPEN (trips open again).
    """

    def __init__(
        self,
        target_id: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.target_id = target_id
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.1, cooldown_seconds)

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: Optional[datetime] = None
        self._probe_in_flight: bool = False

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def can_execute(self, now: Optional[datetime] = None) -> bool:
        """Check whether execution is allowed through the circuit breaker."""
        current_time = now or datetime.now(timezone.utc)

        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            if self._opened_at and (current_time - self._opened_at).total_seconds() >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._probe_in_flight = False
                logger.info(
                    f"CircuitBreaker '{self.target_id}' cooldown elapsed. Transitioned OPEN -> HALF_OPEN"
                )
                return True
            return False

        if self._state == CircuitState.HALF_OPEN:
            # Allow single probe request
            if not self._probe_in_flight:
                return True
            return False

        return False

    def acquire_execution(self, now: Optional[datetime] = None) -> bool:
        """Attempt to acquire permission to execute. In HALF_OPEN, marks probe as in flight."""
        if not self.can_execute(now=now):
            return False

        if self._state == CircuitState.HALF_OPEN:
            self._probe_in_flight = True

        return True

    def record_success(self) -> None:
        """Record a successful execution."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info(
                f"CircuitBreaker '{self.target_id}' probe succeeded. Transitioned HALF_OPEN -> CLOSED"
            )
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._probe_in_flight = False
        elif self._state == CircuitState.CLOSED:
            self._consecutive_failures = 0

    def record_failure(
        self,
        cooldown_seconds: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Record an execution failure."""
        current_time = now or datetime.now(timezone.utc)
        self._consecutive_failures += 1

        if cooldown_seconds is not None and cooldown_seconds > 0:
            self.cooldown_seconds = cooldown_seconds

        if self._state == CircuitState.HALF_OPEN:
            logger.warning(
                f"CircuitBreaker '{self.target_id}' probe failed. Transitioned HALF_OPEN -> OPEN"
            )
            self._state = CircuitState.OPEN
            self._opened_at = current_time
            self._probe_in_flight = False

        elif self._state == CircuitState.CLOSED:
            if self._consecutive_failures >= self.failure_threshold:
                logger.warning(
                    f"CircuitBreaker '{self.target_id}' reached threshold ({self._consecutive_failures}). "
                    f"Transitioned CLOSED -> OPEN for {self.cooldown_seconds}s"
                )
                self._state = CircuitState.OPEN
                self._opened_at = current_time
