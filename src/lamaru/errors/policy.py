"""Policy definitions and actions for standardized error categories."""

from enum import Enum
from typing import Dict, Optional
from pydantic import BaseModel, Field

from lamaru.domain.errors import ErrorCategory


class ErrorAction(str, Enum):
    """Recommended action to resolve an error."""

    RETRY_WITH_BACKOFF = "RETRY_WITH_BACKOFF"
    SWITCH_KEY = "SWITCH_KEY"
    SWITCH_MODEL = "SWITCH_MODEL"
    SWITCH_PROVIDER = "SWITCH_PROVIDER"
    REDUCE_CONTEXT = "REDUCE_CONTEXT"
    DISABLE_KEY = "DISABLE_KEY"
    FAIL = "FAIL"


class ErrorPolicy(BaseModel):
    """Handling policy governing retryability, permanence, cooldown, and routing actions."""

    category: ErrorCategory
    is_retryable: bool
    is_permanent: bool
    requires_key_rotation: bool = False
    requires_model_rotation: bool = False
    requires_provider_rotation: bool = False
    requires_context_reduction: bool = False
    default_cooldown_seconds: float = 0.0
    max_retry_attempts: int = 0
    suggested_action: ErrorAction = ErrorAction.FAIL


class ErrorClassificationResult(BaseModel):
    """Complete diagnostic and policy evaluation for an encountered error."""

    category: ErrorCategory
    policy: ErrorPolicy
    cooldown_seconds: float = 0.0
    message: str = ""
    retry_after_header: Optional[float] = None
    action: ErrorAction = ErrorAction.FAIL


# Default policies for all 10 ErrorCategories as defined in architecture contracts
DEFAULT_POLICIES: Dict[ErrorCategory, ErrorPolicy] = {
    ErrorCategory.AUTH_ERROR: ErrorPolicy(
        category=ErrorCategory.AUTH_ERROR,
        is_retryable=False,
        is_permanent=True,
        requires_key_rotation=True,
        default_cooldown_seconds=0.0,
        max_retry_attempts=0,
        suggested_action=ErrorAction.DISABLE_KEY,
    ),
    ErrorCategory.RATE_LIMIT: ErrorPolicy(
        category=ErrorCategory.RATE_LIMIT,
        is_retryable=True,
        is_permanent=False,
        requires_key_rotation=True,
        default_cooldown_seconds=60.0,
        max_retry_attempts=3,
        suggested_action=ErrorAction.SWITCH_KEY,
    ),
    ErrorCategory.QUOTA_EXCEEDED: ErrorPolicy(
        category=ErrorCategory.QUOTA_EXCEEDED,
        is_retryable=False,
        is_permanent=True,
        requires_key_rotation=True,
        requires_provider_rotation=True,
        default_cooldown_seconds=3600.0,
        max_retry_attempts=0,
        suggested_action=ErrorAction.SWITCH_PROVIDER,
    ),
    ErrorCategory.TEMPORARY_SERVER_ERROR: ErrorPolicy(
        category=ErrorCategory.TEMPORARY_SERVER_ERROR,
        is_retryable=True,
        is_permanent=False,
        default_cooldown_seconds=5.0,
        max_retry_attempts=3,
        suggested_action=ErrorAction.RETRY_WITH_BACKOFF,
    ),
    ErrorCategory.MODEL_NOT_FOUND: ErrorPolicy(
        category=ErrorCategory.MODEL_NOT_FOUND,
        is_retryable=False,
        is_permanent=True,
        requires_model_rotation=True,
        default_cooldown_seconds=0.0,
        max_retry_attempts=0,
        suggested_action=ErrorAction.SWITCH_MODEL,
    ),
    ErrorCategory.INVALID_REQUEST: ErrorPolicy(
        category=ErrorCategory.INVALID_REQUEST,
        is_retryable=False,
        is_permanent=True,
        default_cooldown_seconds=0.0,
        max_retry_attempts=0,
        suggested_action=ErrorAction.FAIL,
    ),
    ErrorCategory.CONTEXT_TOO_LARGE: ErrorPolicy(
        category=ErrorCategory.CONTEXT_TOO_LARGE,
        is_retryable=True,
        is_permanent=False,
        requires_context_reduction=True,
        default_cooldown_seconds=0.0,
        max_retry_attempts=2,
        suggested_action=ErrorAction.REDUCE_CONTEXT,
    ),
    ErrorCategory.SAFETY_BLOCK: ErrorPolicy(
        category=ErrorCategory.SAFETY_BLOCK,
        is_retryable=False,
        is_permanent=True,
        default_cooldown_seconds=0.0,
        max_retry_attempts=0,
        suggested_action=ErrorAction.FAIL,
    ),
    ErrorCategory.NETWORK_ERROR: ErrorPolicy(
        category=ErrorCategory.NETWORK_ERROR,
        is_retryable=True,
        is_permanent=False,
        default_cooldown_seconds=2.0,
        max_retry_attempts=2,
        suggested_action=ErrorAction.RETRY_WITH_BACKOFF,
    ),
    ErrorCategory.UNKNOWN: ErrorPolicy(
        category=ErrorCategory.UNKNOWN,
        is_retryable=False,
        is_permanent=False,
        default_cooldown_seconds=0.0,
        max_retry_attempts=0,
        suggested_action=ErrorAction.FAIL,
    ),
}
