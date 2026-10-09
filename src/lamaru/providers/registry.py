"""Provider registry managing provider adapters, lifecycle, and aggregation."""

import logging
from typing import Dict, List, Optional

from lamaru.domain.models import Model
from lamaru.domain.provider import AIProvider, ProviderHealth

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Central registry and lifecycle manager for all AIProvider instances."""

    def __init__(self) -> None:
        self._providers: Dict[str, AIProvider] = {}

    def register(self, provider: AIProvider, overwrite: bool = False) -> None:
        """Register an AIProvider instance."""
        pid = provider.provider_id
        if pid in self._providers and not overwrite:
            raise ValueError(f"Provider '{pid}' is already registered.")
        self._providers[pid] = provider
        logger.info(f"Registered provider: {pid}")

    def unregister(self, provider_id: str) -> Optional[AIProvider]:
        """Remove a provider from the registry."""
        provider = self._providers.pop(provider_id, None)
        if provider:
            logger.info(f"Unregistered provider: {provider_id}")
        return provider

    def get(self, provider_id: str) -> Optional[AIProvider]:
        """Retrieve a provider by its unique identifier."""
        return self._providers.get(provider_id)

    def list_providers(self, enabled_only: bool = False) -> List[AIProvider]:
        """List registered providers, optionally filtering to only enabled ones."""
        if enabled_only:
            return [p for p in self._providers.values() if p.is_enabled]
        return list(self._providers.values())

    async def list_all_models(self, enabled_only: bool = True) -> List[Model]:
        """Discover and return all models across all registered (enabled) providers."""
        all_models: List[Model] = []
        providers = self.list_providers(enabled_only=enabled_only)

        for provider in providers:
            try:
                models = await provider.list_models()
                all_models.extend(models)
            except Exception as err:
                logger.warning(
                    f"Failed to list models from provider '{provider.provider_id}': {err}"
                )
        return all_models

    async def check_health_all(self) -> Dict[str, ProviderHealth]:
        """Perform health checks across all registered providers."""
        results: Dict[str, ProviderHealth] = {}
        for pid, provider in self._providers.items():
            try:
                results[pid] = await provider.health_check()
            except Exception as err:
                results[pid] = ProviderHealth(
                    provider=pid,
                    status="unhealthy",
                    latency_ms=0.0,
                    message=f"Health check failed with exception: {err}",
                )
        return results

    async def initialize_all(self) -> None:
        """Initialize all providers if an initialize hook is present."""
        for pid, provider in self._providers.items():
            if hasattr(provider, "initialize") and callable(getattr(provider, "initialize")):
                await provider.initialize()  # type: ignore[attr-defined]

    async def shutdown_all(self) -> None:
        """Safely close and clean up all providers if a shutdown hook is present."""
        for pid, provider in self._providers.items():
            if hasattr(provider, "shutdown") and callable(getattr(provider, "shutdown")):
                await provider.shutdown()  # type: ignore[attr-defined]
