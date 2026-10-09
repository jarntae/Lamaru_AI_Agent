"""Error categories and domain exception definitions for Lamaru AI Gateway."""

from enum import Enum
from typing import Any, Optional


class ErrorCategory(str, Enum):
    """Standardized error categories across all providers."""

    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    TEMPORARY_SERVER_ERROR = "TEMPORARY_SERVER_ERROR"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"

    @property
    def is_retryable(self) -> bool:
        """Indicates whether this error type is generally retryable."""
        return self in {
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.TEMPORARY_SERVER_ERROR,
            ErrorCategory.NETWORK_ERROR,
        }

    @property
    def is_permanent(self) -> bool:
        """Indicates whether this error is permanent for the requested parameters."""
        return self in {
            ErrorCategory.AUTH_ERROR,
            ErrorCategory.MODEL_NOT_FOUND,
            ErrorCategory.INVALID_REQUEST,
            ErrorCategory.SAFETY_BLOCK,
        }


class LamaruError(Exception):
    """Base exception class for domain errors in Lamaru AI Gateway."""

    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        key_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        self.provider = provider
        self.model = model
        self.key_id = key_id
        self.details = details or {}

    @property
    def is_retryable(self) -> bool:
        return self.category.is_retryable

    @property
    def is_permanent(self) -> bool:
        return self.category.is_permanent

    def to_dict(self) -> dict[str, Any]:
        """Convert error to a safe dictionary suitable for structured logging/API responses."""
        return {
            "category": self.category.value,
            "message": self.message,
            "provider": self.provider,
            "model": self.model,
            "key_id": self.key_id,
            "is_retryable": self.is_retryable,
            "is_permanent": self.is_permanent,
            "details": self.details,
        }
