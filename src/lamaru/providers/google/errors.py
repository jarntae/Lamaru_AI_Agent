"""Google Gemini API error classification and error handling."""

from typing import Any, Dict, Optional
import httpx

from lamaru.domain.errors import ErrorCategory, LamaruError


def parse_gemini_error(
    status_code: int,
    error_data: Optional[Dict[str, Any]] = None,
    key_id: Optional[str] = None,
    model: Optional[str] = None,
) -> LamaruError:
    """Map Google Gemini HTTP status code and response payload to a normalized LamaruError."""
    details: Dict[str, Any] = error_data or {}
    message = "Unknown Google Gemini error"
    status_str = ""

    if "error" in details and isinstance(details["error"], dict):
        message = details["error"].get("message", message)
        status_str = details["error"].get("status", "")

    # Categorize error
    category = ErrorCategory.UNKNOWN

    if status_code in (401, 403):
        category = ErrorCategory.AUTH_ERROR
    elif status_code == 404:
        category = ErrorCategory.MODEL_NOT_FOUND
    elif status_code == 429:
        if status_str == "RESOURCE_EXHAUSTED" or "quota" in message.lower():
            category = ErrorCategory.RATE_LIMIT
        else:
            category = ErrorCategory.RATE_LIMIT
    elif status_code == 400:
        if "context" in message.lower() or "too large" in message.lower():
            category = ErrorCategory.CONTEXT_TOO_LARGE
        else:
            category = ErrorCategory.INVALID_REQUEST
    elif status_code in (500, 502, 503, 504):
        category = ErrorCategory.TEMPORARY_SERVER_ERROR

    return LamaruError(
        message=message,
        category=category,
        provider="google",
        model=model,
        key_id=key_id,
        details=details,
    )


def handle_network_error(
    exc: Exception,
    key_id: Optional[str] = None,
    model: Optional[str] = None,
) -> LamaruError:
    """Handle network, connection, or timeout exceptions."""
    return LamaruError(
        message=f"Network error communicating with Google Gemini API: {exc}",
        category=ErrorCategory.NETWORK_ERROR,
        provider="google",
        model=model,
        key_id=key_id,
        details={"exception_type": type(exc).__name__},
    )
