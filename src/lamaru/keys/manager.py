"""API Key manager handling multi-key pools, availability, and cooldowns."""

from datetime import datetime
import logging
import os
from typing import Dict, List, Optional

from lamaru.keys.models import APIKeyMetadata, KeyStatus, SecretReference

logger = logging.getLogger(__name__)


class APIKeyManager:
    """Manages pools of API keys per provider, tracking health and availability."""

    def __init__(self) -> None:
        self._keys: Dict[str, APIKeyMetadata] = {}

    def register_key(self, key: APIKeyMetadata, overwrite: bool = False) -> None:
        """Register an API key in the manager."""
        if key.key_id in self._keys and not overwrite:
            raise ValueError(f"API key '{key.key_id}' is already registered.")
        self._keys[key.key_id] = key
        logger.info(f"Registered API key: {key.key_id} (provider: {key.provider})")

    def unregister_key(self, key_id: str) -> Optional[APIKeyMetadata]:
        """Remove an API key from management."""
        key = self._keys.pop(key_id, None)
        if key:
            logger.info(f"Unregistered API key: {key_id}")
        return key

    def get_key(self, key_id: str) -> Optional[APIKeyMetadata]:
        """Retrieve key metadata by internal key_id."""
        return self._keys.get(key_id)

    def get_secret(self, key_id: str) -> Optional[str]:
        """Resolve the raw secret for authentication."""
        key = self._keys.get(key_id)
        if key is None:
            return None
        return key.resolve_secret()

    def list_keys(
        self, provider: Optional[str] = None, enabled_only: bool = False
    ) -> List[APIKeyMetadata]:
        """List managed keys, optionally filtering by provider and enabled status."""
        results: List[APIKeyMetadata] = []
        for key in self._keys.values():
            if provider and key.provider != provider:
                continue
            if enabled_only and not key.enabled:
                continue
            results.append(key)
        return results

    def get_available_keys(
        self, provider: str, now: Optional[datetime] = None
    ) -> List[APIKeyMetadata]:
        """Return all healthy keys for a provider that are not currently in cooldown."""
        candidates = self.list_keys(provider=provider, enabled_only=True)
        return [k for k in candidates if k.is_available(now=now)]

    def record_key_success(self, key_id: str) -> None:
        """Record a successful request for a key."""
        key = self._keys.get(key_id)
        if key:
            key.record_success()

    def record_key_failure(
        self,
        key_id: str,
        error_msg: str,
        cooldown_seconds: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Record a failure for a key and place it in cooldown if specified."""
        key = self._keys.get(key_id)
        if key:
            key.record_failure(error_msg, cooldown_seconds=cooldown_seconds, now=now)

    def load_from_env(self, provider: str, prefix: str = "GEMINI_API_KEY") -> int:
        """Automatically discover and register keys from environment variables.

        Supports both numbered patterns (e.g. GEMINI_API_KEY_01, GEMINI_API_KEY_02)
        and single-key pattern (e.g. GEMINI_API_KEY).
        """
        loaded_count = 0

        # Check numbered pattern e.g. PREFIX_01 ... PREFIX_99
        for i in range(1, 100):
            env_var = f"{prefix}_{i:02d}"
            if env_var in os.environ and os.environ[env_var].strip():
                key_id = f"{provider}-key-{i:02d}"
                meta = APIKeyMetadata(
                    key_id=key_id,
                    provider=provider,
                    secret_ref=SecretReference.from_env(env_var),
                )
                self.register_key(meta, overwrite=True)
                loaded_count += 1

        # Check base single-key pattern if no numbered keys found
        if loaded_count == 0 and prefix in os.environ and os.environ[prefix].strip():
            key_id = f"{provider}-key-01"
            meta = APIKeyMetadata(
                key_id=key_id,
                provider=provider,
                secret_ref=SecretReference.from_env(prefix),
            )
            self.register_key(meta, overwrite=True)
            loaded_count += 1

        return loaded_count
