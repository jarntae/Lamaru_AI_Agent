import pytest

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.errors.classifier import ErrorClassifier, create_fake_error_response
from lamaru.errors.policy import DEFAULT_POLICIES, ErrorAction


def test_all_10_categories_have_policies():
    """Verify every ErrorCategory has a complete policy defined."""
    for category in ErrorCategory:
        assert category in DEFAULT_POLICIES
        policy = DEFAULT_POLICIES[category]
        assert policy.category == category
        assert isinstance(policy.is_retryable, bool)
        assert isinstance(policy.is_permanent, bool)
        assert isinstance(policy.suggested_action, ErrorAction)


def test_classify_fake_responses():
    classifier = ErrorClassifier()

    # 1. AUTH_ERROR
    code, body, headers = create_fake_error_response(ErrorCategory.AUTH_ERROR)
    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.AUTH_ERROR
    assert res.policy.is_retryable is False
    assert res.policy.is_permanent is True
    assert res.action == ErrorAction.DISABLE_KEY

    # 2. RATE_LIMIT
    code, body, headers = create_fake_error_response(ErrorCategory.RATE_LIMIT)
    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.RATE_LIMIT
    assert res.policy.is_retryable is True
    assert res.action == ErrorAction.SWITCH_KEY
    assert res.cooldown_seconds == 60.0

    # 3. QUOTA_EXCEEDED
    code, body, headers = create_fake_error_response(ErrorCategory.QUOTA_EXCEEDED)
    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.QUOTA_EXCEEDED
    assert res.policy.requires_provider_rotation is True
    assert res.action == ErrorAction.SWITCH_PROVIDER

    # 4. TEMPORARY_SERVER_ERROR
    code, body, headers = create_fake_error_response(ErrorCategory.TEMPORARY_SERVER_ERROR)
    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.TEMPORARY_SERVER_ERROR
    assert res.policy.is_retryable is True
    assert res.action == ErrorAction.RETRY_WITH_BACKOFF

    # 5. MODEL_NOT_FOUND
    code, body, headers = create_fake_error_response(ErrorCategory.MODEL_NOT_FOUND)
    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.MODEL_NOT_FOUND
    assert res.policy.requires_model_rotation is True
    assert res.action == ErrorAction.SWITCH_MODEL

    # 6. INVALID_REQUEST
    code, body, headers = create_fake_error_response(ErrorCategory.INVALID_REQUEST)
    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.INVALID_REQUEST
    assert res.policy.is_retryable is False
    assert res.action == ErrorAction.FAIL

    # 7. CONTEXT_TOO_LARGE
    code, body, headers = create_fake_error_response(ErrorCategory.CONTEXT_TOO_LARGE)
    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.CONTEXT_TOO_LARGE
    assert res.policy.requires_context_reduction is True
    assert res.action == ErrorAction.REDUCE_CONTEXT

    # 8. SAFETY_BLOCK
    lamaru_safety_err = LamaruError("Blocked by safety", category=ErrorCategory.SAFETY_BLOCK)
    res = classifier.classify_exception(lamaru_safety_err)
    assert res.category == ErrorCategory.SAFETY_BLOCK
    assert res.policy.is_permanent is True
    assert res.policy.is_retryable is False
    assert res.action == ErrorAction.FAIL

    # 9. NETWORK_ERROR
    res = classifier.classify_exception(ConnectionResetError("Connection lost"))
    assert res.category == ErrorCategory.NETWORK_ERROR
    assert res.policy.is_retryable is True
    assert res.action == ErrorAction.RETRY_WITH_BACKOFF

    # 10. UNKNOWN
    res = classifier.classify_exception(RuntimeError("Unexpected runtime fault"))
    assert res.category == ErrorCategory.UNKNOWN
    assert res.action == ErrorAction.FAIL


def test_retry_after_header_handling():
    classifier = ErrorClassifier()
    code, body, headers = create_fake_error_response(
        ErrorCategory.RATE_LIMIT, retry_after_seconds=42
    )

    res = classifier.classify_http(code, body, headers)
    assert res.category == ErrorCategory.RATE_LIMIT
    assert res.retry_after_header == 42.0
    assert res.cooldown_seconds == 42.0  # Overrides default 60.0
