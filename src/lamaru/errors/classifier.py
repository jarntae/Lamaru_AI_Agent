"""Central ErrorClassifier engine for categorizing errors and determining policy actions."""

from typing import Any, Dict, Optional, Tuple
import re

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.errors.policy import (
    DEFAULT_POLICIES,
    ErrorAction,
    ErrorClassificationResult,
    ErrorPolicy,
)


class ErrorClassifier:
    """Classifies errors, extracts retry delays, and applies routing policies."""

    def __init__(self, custom_policies: Optional[Dict[ErrorCategory, ErrorPolicy]] = None) -> None:
        self._policies: Dict[ErrorCategory, ErrorPolicy] = dict(DEFAULT_POLICIES)
        if custom_policies:
            self._policies.update(custom_policies)

    def get_policy(self, category: ErrorCategory) -> ErrorPolicy:
        """Retrieve policy for a specific error category."""
        return self._policies.get(category, self._policies[ErrorCategory.UNKNOWN])

    def parse_retry_after(self, headers: Optional[Dict[str, str]]) -> Optional[float]:
        """Extract seconds from a Retry-After header if present."""
        if not headers:
            return None

        # Case-insensitive header lookup
        retry_val: Optional[str] = None
        for k, v in headers.items():
            if k.lower() == "retry-after":
                retry_val = v.strip()
                break

        if not retry_val:
            return None

        try:
            return float(retry_val)
        except ValueError:
            return None

    def classify_http(
        self,
        status_code: int,
        response_body: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        message: Optional[str] = None,
    ) -> ErrorClassificationResult:
        """Classify HTTP response into an ErrorClassificationResult."""
        err_msg = message or ""
        status_str = ""

        # Extract message and status from JSON error body if available
        if isinstance(response_body, dict):
            if "error" in response_body and isinstance(response_body["error"], dict):
                err_msg = response_body["error"].get("message", err_msg)
                status_str = response_body["error"].get("status", "")
            elif "message" in response_body:
                err_msg = str(response_body["message"])

        body_text_lower = (str(response_body) + " " + err_msg).lower()

        # Classification logic
        if status_code in (401, 403):
            category = ErrorCategory.AUTH_ERROR
        elif status_code == 404:
            category = ErrorCategory.MODEL_NOT_FOUND
        elif status_code == 429:
            if "daily" in body_text_lower or "monthly" in body_text_lower or "exhausted" in status_str.lower():
                category = ErrorCategory.QUOTA_EXCEEDED
            else:
                category = ErrorCategory.RATE_LIMIT
        elif status_code == 400:
            if "context" in body_text_lower or "too large" in body_text_lower or "token" in body_text_lower:
                category = ErrorCategory.CONTEXT_TOO_LARGE
            else:
                category = ErrorCategory.INVALID_REQUEST
        elif status_code in (500, 502, 503, 504):
            category = ErrorCategory.TEMPORARY_SERVER_ERROR
        else:
            category = ErrorCategory.UNKNOWN

        policy = self.get_policy(category)
        retry_after = self.parse_retry_after(headers)
        cooldown = retry_after if retry_after is not None else policy.default_cooldown_seconds

        return ErrorClassificationResult(
            category=category,
            policy=policy,
            cooldown_seconds=cooldown,
            message=err_msg or f"HTTP Error {status_code}",
            retry_after_header=retry_after,
            action=policy.suggested_action,
        )

    def classify_exception(self, exc: Exception) -> ErrorClassificationResult:
        """Classify a Python exception into an ErrorClassificationResult."""
        if isinstance(exc, LamaruError):
            policy = self.get_policy(exc.category)
            return ErrorClassificationResult(
                category=exc.category,
                policy=policy,
                cooldown_seconds=policy.default_cooldown_seconds,
                message=exc.message,
                action=policy.suggested_action,
            )

        exc_name = type(exc).__name__.lower()
        exc_str = str(exc).lower()

        if "timeout" in exc_name or "connection" in exc_name or "network" in exc_name:
            category = ErrorCategory.NETWORK_ERROR
        elif "auth" in exc_name or "forbidden" in exc_name:
            category = ErrorCategory.AUTH_ERROR
        else:
            category = ErrorCategory.UNKNOWN

        policy = self.get_policy(category)
        return ErrorClassificationResult(
            category=category,
            policy=policy,
            cooldown_seconds=policy.default_cooldown_seconds,
            message=str(exc),
            action=policy.suggested_action,
        )


def create_fake_error_response(
    category: ErrorCategory,
    retry_after_seconds: Optional[int] = None,
) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
    """Factory creating realistic fake HTTP responses for any of the 10 ErrorCategories."""
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)

    if category == ErrorCategory.AUTH_ERROR:
        return (
            401,
            {
                "error": {
                    "code": 401,
                    "message": "Invalid API key or credentials provided.",
                    "status": "UNAUTHENTICATED",
                }
            },
            headers,
        )

    elif category == ErrorCategory.RATE_LIMIT:
        return (
            429,
            {
                "error": {
                    "code": 429,
                    "message": "Too many requests per minute. Please back off.",
                    "status": "RATE_LIMIT_EXCEEDED",
                }
            },
            headers,
        )

    elif category == ErrorCategory.QUOTA_EXCEEDED:
        return (
            429,
            {
                "error": {
                    "code": 429,
                    "message": "Daily request quota has been exhausted.",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
            headers,
        )

    elif category == ErrorCategory.TEMPORARY_SERVER_ERROR:
        return (
            503,
            {
                "error": {
                    "code": 503,
                    "message": "Server temporarily overloaded, please retry.",
                    "status": "UNAVAILABLE",
                }
            },
            headers,
        )

    elif category == ErrorCategory.MODEL_NOT_FOUND:
        return (
            404,
            {
                "error": {
                    "code": 404,
                    "message": "The requested model does not exist.",
                    "status": "NOT_FOUND",
                }
            },
            headers,
        )

    elif category == ErrorCategory.INVALID_REQUEST:
        return (
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Invalid argument: temperature must be <= 2.0",
                    "status": "INVALID_ARGUMENT",
                }
            },
            headers,
        )

    elif category == ErrorCategory.CONTEXT_TOO_LARGE:
        return (
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Request context is too large: 150000 tokens exceed maximum 128000.",
                    "status": "INVALID_ARGUMENT",
                }
            },
            headers,
        )

    elif category == ErrorCategory.SAFETY_BLOCK:
        return (
            200,
            {
                "candidates": [
                    {
                        "finishReason": "SAFETY",
                        "safetyRatings": [{"category": "HARM", "probability": "HIGH"}],
                    }
                ]
            },
            headers,
        )

    elif category == ErrorCategory.NETWORK_ERROR:
        return (
            0,
            {"error": {"code": 0, "message": "Failed to connect to host", "status": "NETWORK_ERROR"}},
            headers,
        )

    else:  # UNKNOWN
        return (
            520,
            {"error": {"code": 520, "message": "Unknown server error occurred", "status": "UNKNOWN"}},
            headers,
        )
