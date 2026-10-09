"""Retry engine implementing exponential backoff, jitter, and error-policy compliance."""

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Optional
from pydantic import BaseModel, Field

from lamaru.domain.errors import LamaruError
from lamaru.errors.classifier import ErrorClassifier

logger = logging.getLogger(__name__)


class RetryPolicy(BaseModel):
    """Configuration for retry execution."""

    max_attempts: int = Field(default=3, ge=1, description="Maximum total attempts")
    initial_backoff_seconds: float = Field(default=0.5, gt=0, description="Base backoff time")
    backoff_multiplier: float = Field(default=2.0, ge=1.0, description="Multiplier per attempt")
    max_backoff_seconds: float = Field(default=10.0, gt=0, description="Maximum ceiling backoff")
    jitter: bool = Field(default=True, description="Whether to apply random jitter")
    jitter_min: float = Field(default=0.8, ge=0.0, description="Minimum jitter factor")
    jitter_max: float = Field(default=1.2, ge=1.0, description="Maximum jitter factor")

    def calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with optional jitter for a given attempt index (1-based)."""
        # attempt 1 = initial_backoff * (multiplier ^ 0) = initial_backoff
        power = max(0, attempt - 1)
        backoff = min(
            self.max_backoff_seconds,
            self.initial_backoff_seconds * (self.backoff_multiplier ** power),
        )
        if self.jitter:
            factor = random.uniform(self.jitter_min, self.jitter_max)
            backoff *= factor

        return max(0.01, backoff)


async def execute_with_retry(
    operation: Callable[[], Awaitable[Any]],
    error_classifier: Optional[ErrorClassifier] = None,
    retry_policy: Optional[RetryPolicy] = None,
    on_retry: Optional[Callable[[Exception, int, float], Any]] = None,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    """Execute an async operation with automatic retry honoring ErrorPolicy and backoff.

    Rules:
    - If the operation succeeds, return its result immediately.
    - If the operation fails with a permanent error (AUTH_ERROR, INVALID_REQUEST, SAFETY_BLOCK),
      abort immediately without retrying.
    - If the error is retryable (RATE_LIMIT, TEMPORARY_SERVER_ERROR, NETWORK_ERROR),
      wait backoff (or Retry-After header) and retry up to max_attempts.
    - If max_attempts is exhausted, re-raise the final exception.
    """
    classifier = error_classifier or ErrorClassifier()
    policy = retry_policy or RetryPolicy()

    last_exception: Optional[Exception] = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            last_exception = exc

            # Classify error to determine retryability
            classification = classifier.classify_exception(exc)

            # Permanent / non-retryable error -> Abort immediately
            if not classification.policy.is_retryable or classification.policy.is_permanent:
                logger.warning(
                    f"Non-retryable error ({classification.category.value}): {exc}. Aborting without retry."
                )
                raise exc

            # If attempts exhausted -> Abort
            if attempt >= policy.max_attempts:
                logger.warning(
                    f"Max retry attempts ({policy.max_attempts}) exhausted for ({classification.category.value}): {exc}"
                )
                raise exc

            # Determine delay: use Retry-After if available, otherwise exponential backoff
            if classification.retry_after_header is not None and classification.retry_after_header > 0:
                delay = classification.retry_after_header
            else:
                delay = policy.calculate_backoff(attempt)

            logger.info(
                f"Attempt {attempt}/{policy.max_attempts} failed with {classification.category.value}. "
                f"Retrying in {delay:.2f}s..."
            )

            if on_retry is not None:
                callback_result = on_retry(exc, attempt, delay)
                if asyncio.iscoroutine(callback_result):
                    await callback_result

            await sleep_func(delay)

    # Fallback safety (should not be reached)
    if last_exception:
        raise last_exception
