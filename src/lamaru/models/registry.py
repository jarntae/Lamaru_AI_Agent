"""Model registry for indexing, discovering, and filtering AI models."""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from lamaru.domain.models import Model, ModelCapability, TaskRequirement
from lamaru.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Central registry and query engine for model metadata across all providers."""

    def __init__(self) -> None:
        # Key: (provider_id, model_id)
        self._models: Dict[Tuple[str, str], Model] = {}
        # Key: (provider_id, model_id) -> override fields
        self._overrides: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _apply_override(self, model: Model) -> Model:
        key = (model.provider, model.model_id)
        if key not in self._overrides:
            return model

        overrides = self._overrides[key]
        model_data = model.model_dump()
        model_data.update(overrides)
        return Model(**model_data)

    def register(self, model: Model, overwrite: bool = False) -> None:
        """Register a normalized Model into the registry."""
        key = (model.provider, model.model_id)
        if key in self._models and not overwrite:
            raise ValueError(
                f"Model '{model.model_id}' for provider '{model.provider}' is already registered."
            )
        self._models[key] = model
        logger.info(f"Registered model: {model.provider}/{model.model_id}")

    def unregister(self, provider: str, model_id: str) -> Optional[Model]:
        """Remove a model from the registry."""
        key = (provider, model_id)
        model = self._models.pop(key, None)
        self._overrides.pop(key, None)
        return model

    def get(self, provider: str, model_id: str) -> Optional[Model]:
        """Retrieve model metadata by provider and model_id."""
        key = (provider, model_id)
        model = self._models.get(key)
        if model is None:
            return None
        return self._apply_override(model)

    def get_by_id(self, model_id: str) -> Optional[Model]:
        """Find the first matching model matching model_id across any provider."""
        clean_id = model_id.replace("models/", "")
        for (provider, mid), model in self._models.items():
            if mid == clean_id:
                return self._apply_override(model)
        return None

    def list_models(
        self, enabled_only: bool = True, provider: Optional[str] = None
    ) -> List[Model]:
        """List all models registered in the system."""
        results: List[Model] = []
        for model in self._models.values():
            resolved = self._apply_override(model)
            if provider and resolved.provider != provider:
                continue
            if enabled_only and not resolved.enabled:
                continue
            results.append(resolved)
        return results

    def find_by_capabilities(
        self, capabilities: Set[ModelCapability], enabled_only: bool = True
    ) -> List[Model]:
        """Find models supporting all of the requested capabilities."""
        models = self.list_models(enabled_only=enabled_only)
        matched: List[Model] = []
        for m in models:
            if capabilities.issubset(m.capabilities):
                matched.append(m)
        return matched

    def filter_candidates(
        self,
        requirement: TaskRequirement,
        min_context_window: Optional[int] = None,
        enabled_only: bool = True,
    ) -> List[Model]:
        """Filter candidate models matching a task's requirements."""
        candidates = self.list_models(enabled_only=enabled_only)
        matched: List[Model] = []

        # Derive required capabilities from requirement flags
        required_caps = set(requirement.required_capabilities)
        if requirement.requires_tools:
            required_caps.add(ModelCapability.TOOL_CALLING)
        if requirement.requires_reasoning:
            required_caps.add(ModelCapability.REASONING)
        if requirement.requires_long_context:
            required_caps.add(ModelCapability.LONG_CONTEXT)

        for m in candidates:
            # Check capabilities
            if not required_caps.issubset(m.capabilities):
                continue
            # Check minimum context window
            if min_context_window and m.context_window < min_context_window:
                continue
            matched.append(m)

        return matched

    def set_override(
        self,
        provider: str,
        model_id: str,
        enabled: Optional[bool] = None,
        priority: Optional[int] = None,
        context_window: Optional[int] = None,
    ) -> None:
        """Set local overrides for a model's settings."""
        key = (provider, model_id)
        if key not in self._overrides:
            self._overrides[key] = {}

        if enabled is not None:
            self._overrides[key]["enabled"] = enabled
        if priority is not None:
            self._overrides[key]["priority"] = priority
        if context_window is not None:
            self._overrides[key]["context_window"] = context_window

    def clear_override(self, provider: str, model_id: str) -> None:
        """Clear local overrides for a model."""
        self._overrides.pop((provider, model_id), None)

    async def sync_from_providers(
        self, provider_registry: ProviderRegistry, enabled_only: bool = True
    ) -> int:
        """Discover models from all registered providers and index them into the registry."""
        models = await provider_registry.list_all_models(enabled_only=enabled_only)
        synced_count = 0
        for m in models:
            self.register(m, overwrite=True)
            synced_count += 1
        return synced_count
