"""Error classification, policies, and handling for Lamaru AI Gateway."""

from lamaru.errors.classifier import ErrorClassifier, create_fake_error_response
from lamaru.errors.policy import (
    DEFAULT_POLICIES,
    ErrorAction,
    ErrorClassificationResult,
    ErrorPolicy,
)

__all__ = [
    "ErrorClassifier",
    "ErrorPolicy",
    "ErrorAction",
    "ErrorClassificationResult",
    "DEFAULT_POLICIES",
    "create_fake_error_response",
]
