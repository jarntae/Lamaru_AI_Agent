import asyncio
import pytest

from lamaru.domain.models import Model, ModelCapability, TaskRequirement
from lamaru.models.registry import ModelRegistry
from lamaru.providers.mock import MockProvider
from lamaru.providers.registry import ProviderRegistry


def test_model_registration_and_get():
    registry = ModelRegistry()
    model = Model(
        provider="google",
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        capabilities={ModelCapability.CODING, ModelCapability.FAST},
        context_window=1000000,
        priority=10,
    )

    registry.register(model)
    assert registry.get("google", "gemini-2.5-flash") == model
    assert registry.get_by_id("gemini-2.5-flash") == model
    assert registry.get_by_id("models/gemini-2.5-flash") == model
    assert registry.get("google", "nonexistent") is None

    # Unregister
    removed = registry.unregister("google", "gemini-2.5-flash")
    assert removed == model
    assert registry.get("google", "gemini-2.5-flash") is None


def test_duplicate_registration():
    registry = ModelRegistry()
    m = Model(
        provider="google",
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        context_window=1000000,
    )
    registry.register(m)

    with pytest.raises(ValueError):
        registry.register(m)

    # Overwrite works
    registry.register(m, overwrite=True)
    assert len(registry.list_models()) == 1


def test_find_by_capabilities():
    registry = ModelRegistry()
    m1 = Model(
        provider="google",
        model_id="flash",
        display_name="Flash",
        capabilities={ModelCapability.CODING, ModelCapability.FAST},
        context_window=50000,
    )
    m2 = Model(
        provider="google",
        model_id="pro",
        display_name="Pro",
        capabilities={ModelCapability.CODING, ModelCapability.REASONING},
        context_window=100000,
    )
    m3 = Model(
        provider="local",
        model_id="local-fast",
        display_name="Local Fast",
        capabilities={ModelCapability.FAST},
        context_window=8000,
    )

    registry.register(m1)
    registry.register(m2)
    registry.register(m3)

    coding_models = registry.find_by_capabilities({ModelCapability.CODING})
    assert {m.model_id for m in coding_models} == {"flash", "pro"}

    coding_fast = registry.find_by_capabilities(
        {ModelCapability.CODING, ModelCapability.FAST}
    )
    assert [m.model_id for m in coding_fast] == ["flash"]

    reasoning = registry.find_by_capabilities({ModelCapability.REASONING})
    assert [m.model_id for m in reasoning] == ["pro"]


def test_filter_candidates_with_task_requirement():
    registry = ModelRegistry()
    m1 = Model(
        provider="google",
        model_id="flash",
        display_name="Flash",
        capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
        context_window=50000,
    )
    m2 = Model(
        provider="google",
        model_id="pro",
        display_name="Pro",
        capabilities={
            ModelCapability.CODING,
            ModelCapability.TOOL_CALLING,
            ModelCapability.REASONING,
            ModelCapability.LONG_CONTEXT,
        },
        context_window=200000,
    )

    registry.register(m1)
    registry.register(m2)

    # Requires reasoning and tools
    req = TaskRequirement(requires_tools=True, requires_reasoning=True)
    candidates = registry.filter_candidates(req)
    assert len(candidates) == 1
    assert candidates[0].model_id == "pro"

    # Context window requirement filter
    req_simple = TaskRequirement(requires_tools=True)
    candidates_all = registry.filter_candidates(req_simple)
    assert len(candidates_all) == 2

    candidates_large_ctx = registry.filter_candidates(
        req_simple, min_context_window=100000
    )
    assert len(candidates_large_ctx) == 1
    assert candidates_large_ctx[0].model_id == "pro"


def test_model_overrides():
    registry = ModelRegistry()
    m = Model(
        provider="google",
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        context_window=1000000,
        priority=5,
        enabled=True,
    )
    registry.register(m)

    # Override priority and disable
    registry.set_override("google", "gemini-2.5-flash", enabled=False, priority=99)

    # When enabled_only=True, it should not appear
    assert len(registry.list_models(enabled_only=True)) == 0

    # When fetched directly, overrides are applied
    overridden = registry.get("google", "gemini-2.5-flash")
    assert overridden is not None
    assert overridden.enabled is False
    assert overridden.priority == 99

    # Clear override
    registry.clear_override("google", "gemini-2.5-flash")
    restored = registry.get("google", "gemini-2.5-flash")
    assert restored is not None
    assert restored.enabled is True
    assert restored.priority == 5


def test_sync_from_providers_dynamic():
    """Verify models are discovered dynamically from provider registry rather than only hardcoded."""
    async def _test():
        provider_registry = ProviderRegistry()
        p1 = MockProvider(
            provider_id="mock-provider",
            models=[
                Model(
                    provider="mock-provider",
                    model_id="dynamic-1",
                    display_name="Dynamic 1",
                    capabilities={ModelCapability.CODING},
                    context_window=32000,
                ),
                Model(
                    provider="mock-provider",
                    model_id="dynamic-2",
                    display_name="Dynamic 2",
                    capabilities={ModelCapability.FAST},
                    context_window=16000,
                ),
            ],
        )
        provider_registry.register(p1)

        model_registry = ModelRegistry()
        synced_count = await model_registry.sync_from_providers(provider_registry)
        assert synced_count == 2

        models = model_registry.list_models()
        assert len(models) == 2
        assert {m.model_id for m in models} == {"dynamic-1", "dynamic-2"}

    asyncio.run(_test())
