"""API Key metadata, safe secret referencing, and key status representation."""

from datetime import datetime, timezone, timedelta
from enum import Enum
import os
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, PrivateAttr


class KeyStatus(str, Enum):
    """Operational status of an API Key."""

    HEALTHY = "HEALTHY"
    COOLDOWN = "COOLDOWN"
    DISABLED = "DISABLED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_FAILED = "AUTH_FAILED"


class SecretReference:
    """Safe wrapper around raw secrets or environment variable references.

    Guarantees raw secrets are never leaked in logs, __repr__, or __str__.
    """

    def __init__(self, ref_str: str, raw_secret: Optional[str] = None) -> None:
        self.ref_str = ref_str
        self._raw_secret = raw_secret

    @classmethod
    def from_env(cls, env_var: str) -> "SecretReference":
        """Create a SecretReference linked to an environment variable."""
        return cls(ref_str=f"env:{env_var}")

    @classmethod
    def from_raw(cls, secret: str, label: str = "in-memory") -> "SecretReference":
        """Create a SecretReference from a raw secret (masked in representations)."""
        return cls(ref_str=f"raw:{label}", raw_secret=secret)

    def resolve(self) -> str:
        """Retrieve the actual plaintext secret string."""
        if self._raw_secret is not None:
            return self._raw_secret

        if self.ref_str.startswith("env:"):
            env_var = self.ref_str[len("env:") :]
            val = os.environ.get(env_var, "")
            return val

        return ""

    def __repr__(self) -> str:
        return f"<SecretReference ref={self.ref_str} secret=***REDACTED***>"

    def __str__(self) -> str:
        return f"{self.ref_str} (protected)"


class APIKeyMetadata(BaseModel):
    """Metadata tracking health, quota, cooldown, and identity of an API key."""

    key_id: str = Field(..., description="Internal key identifier (e.g. google-key-01)")
    provider: str = Field(..., description="Provider identifier (e.g. google)")
    ref_string: str = Field(..., description="Secret reference string (e.g. env:GEMINI_API_KEY_01)")
    enabled: bool = Field(default=True, description="Whether this key is enabled")
    status: KeyStatus = Field(default=KeyStatus.HEALTHY, description="Current health status")
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    cooldown_until: Optional[datetime] = Field(default=None)
    last_error: Optional[str] = Field(default=None)
    last_used_at: Optional[datetime] = Field(default=None)

    _secret_ref: Optional[SecretReference] = PrivateAttr(default=None)

    def __init__(
        self,
        key_id: str,
        provider: str,
        secret_ref: Optional[SecretReference] = None,
        ref_string: Optional[str] = None,
        **data: Any,
    ) -> None:
        if secret_ref is not None:
            ref_str = secret_ref.ref_str
        elif ref_string is not None:
            ref_str = ref_string
            secret_ref = SecretReference(ref_str)
        else:
            raise ValueError("Either secret_ref or ref_string must be provided.")

        super().__init__(key_id=key_id, provider=provider, ref_string=ref_str, **data)
        self._secret_ref = secret_ref

    @property
    def secret_ref(self) -> SecretReference:
        if self._secret_ref is None:
            self._secret_ref = SecretReference(self.ref_string)
        return self._secret_ref

    def resolve_secret(self) -> str:
        """Resolve the plaintext secret for provider authentication."""
        return self.secret_ref.resolve()

    def is_available(self, now: Optional[datetime] = None) -> bool:
        """Check whether the key is currently available for routing."""
        if not self.enabled:
            return False

        if self.status in (KeyStatus.DISABLED, KeyStatus.AUTH_FAILED):
            return False

        current_time = now or datetime.now(timezone.utc)

        if self.status == KeyStatus.COOLDOWN:
            if self.cooldown_until and current_time >= self.cooldown_until:
                # Cooldown period expired, key returns to healthy
                self.status = KeyStatus.HEALTHY
                self.cooldown_until = None
                return True
            return False

        return self.status == KeyStatus.HEALTHY

    def set_cooldown(self, seconds: float, now: Optional[datetime] = None) -> None:
        """Place this key in cooldown for a specified duration."""
        current_time = now or datetime.now(timezone.utc)
        self.status = KeyStatus.COOLDOWN
        self.cooldown_until = current_time + timedelta(seconds=seconds)

    def record_success(self, now: Optional[datetime] = None) -> None:
        """Record successful invocation using this key."""
        current_time = now or datetime.now(timezone.utc)
        self.success_count += 1
        self.last_used_at = current_time
        if self.status == KeyStatus.COOLDOWN:
            self.status = KeyStatus.HEALTHY
            self.cooldown_until = None

    def record_failure(
        self,
        error_msg: str,
        cooldown_seconds: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Record an error and optionally put the key into cooldown."""
        current_time = now or datetime.now(timezone.utc)
        self.failure_count += 1
        self.last_error = error_msg
        self.last_used_at = current_time

        if cooldown_seconds and cooldown_seconds > 0:
            self.set_cooldown(cooldown_seconds, now=current_time)

    def to_safe_dict(self) -> Dict[str, Any]:
        """Convert to safe dictionary representation with absolutely no raw secrets."""
        return {
            "key_id": self.key_id,
            "provider": self.provider,
            "ref_string": self.ref_string,
            "enabled": self.enabled,
            "status": self.status.value,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "cooldown_until": (
                self.cooldown_until.isoformat() if self.cooldown_until else None
            ),
            "last_error": self.last_error,
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
        }
