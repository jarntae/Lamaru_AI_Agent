from datetime import datetime, timezone, timedelta
import os
import pytest

from lamaru.keys.manager import APIKeyManager
from lamaru.keys.models import APIKeyMetadata, KeyStatus, SecretReference


def test_multi_key_registration_and_retrieval():
    manager = APIKeyManager()

    key1 = APIKeyMetadata(
        key_id="google-key-01",
        provider="google",
        secret_ref=SecretReference.from_raw("secret_1"),
    )
    key2 = APIKeyMetadata(
        key_id="google-key-02",
        provider="google",
        secret_ref=SecretReference.from_raw("secret_2"),
    )
    key3 = APIKeyMetadata(
        key_id="google-key-03",
        provider="google",
        secret_ref=SecretReference.from_raw("secret_3"),
    )

    manager.register_key(key1)
    manager.register_key(key2)
    manager.register_key(key3)

    keys = manager.list_keys(provider="google")
    assert len(keys) == 3
    assert {k.key_id for k in keys} == {
        "google-key-01",
        "google-key-02",
        "google-key-03",
    }
    assert manager.get_secret("google-key-01") == "secret_1"
    assert manager.get_secret("google-key-02") == "secret_2"
    assert manager.get_secret("google-key-03") == "secret_3"


def test_secret_masking_security():
    secret = "AIzaSyD-super-secret-google-key-999"
    ref = SecretReference.from_raw(secret, label="test-key")

    # Security: Secret must NEVER appear in representations
    assert secret not in str(ref)
    assert secret not in repr(ref)
    assert "***REDACTED***" in repr(ref)

    meta = APIKeyMetadata(
        key_id="google-key-01",
        provider="google",
        secret_ref=ref,
    )

    safe_dict = meta.to_safe_dict()
    assert secret not in str(safe_dict)
    assert safe_dict["key_id"] == "google-key-01"

    # Secret can be resolved explicitly for API calls
    assert meta.resolve_secret() == secret


def test_secret_reference_from_env():
    os.environ["LAMARU_TEST_SECRET"] = "test_env_secret_value"
    try:
        ref = SecretReference.from_env("LAMARU_TEST_SECRET")
        assert ref.resolve() == "test_env_secret_value"
        assert "test_env_secret_value" not in str(ref)
        assert "test_env_secret_value" not in repr(ref)
    finally:
        os.environ.pop("LAMARU_TEST_SECRET", None)


def test_cooldown_and_availability():
    manager = APIKeyManager()
    key1 = APIKeyMetadata(
        key_id="google-key-01",
        provider="google",
        secret_ref=SecretReference.from_raw("secret_1"),
    )
    manager.register_key(key1)

    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
    assert key1.is_available(now=now) is True

    # Place key1 in cooldown for 60 seconds
    manager.record_key_failure(
        "google-key-01",
        error_msg="Rate limit 429",
        cooldown_seconds=60,
        now=now,
    )

    assert key1.status == KeyStatus.COOLDOWN
    assert key1.failure_count == 1
    assert key1.is_available(now=now) is False

    available_keys = manager.get_available_keys("google", now=now)
    assert len(available_keys) == 0

    # Simulate 30 seconds later: still in cooldown
    t_30s = now + timedelta(seconds=30)
    assert key1.is_available(now=t_30s) is False

    # Simulate 65 seconds later: cooldown expired, key is healthy and available again
    t_65s = now + timedelta(seconds=65)
    assert key1.is_available(now=t_65s) is True
    assert key1.status == KeyStatus.HEALTHY
    assert len(manager.get_available_keys("google", now=t_65s)) == 1


def test_success_record():
    manager = APIKeyManager()
    key1 = APIKeyMetadata(
        key_id="google-key-01",
        provider="google",
        secret_ref=SecretReference.from_raw("secret_1"),
    )
    manager.register_key(key1)

    manager.record_key_success("google-key-01")
    assert key1.success_count == 1
    assert key1.last_used_at is not None


def test_load_from_env_numbered():
    os.environ["MOCK_KEY_01"] = "val_01"
    os.environ["MOCK_KEY_02"] = "val_02"

    try:
        manager = APIKeyManager()
        loaded = manager.load_from_env(provider="google", prefix="MOCK_KEY")
        assert loaded == 2

        assert manager.get_secret("google-key-01") == "val_01"
        assert manager.get_secret("google-key-02") == "val_02"
    finally:
        os.environ.pop("MOCK_KEY_01", None)
        os.environ.pop("MOCK_KEY_02", None)
