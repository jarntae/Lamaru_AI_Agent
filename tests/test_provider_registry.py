import asyncio
import pytest

from lamaru.domain.models import Model, ModelCapability
from lamaru.domain.provider import AIProvider
from lamaru.providers.mock import MockProvider
from lamaru.providers.registry import ProviderRegistry


def test_registry_registration_and_retrieval():
    registry = ProviderRegistry()
    provider_a = MockProvider(provider_id="provider-a")

    registry.register(provider_a)
    assert registry.get("provider-a") is provider_a
    assert len(registry.list_providers()) == 1

    # Duplicate registration without overwrite should fail
    with pytest.raises(ValueError):
        registry.register(MockProvider(provider_id="provider-a"))

    # Overwrite registration should succeed
    new_provider_a = MockProvider(provider_id="provider-a")
    registry.register(new_provider_a, overwrite=True)
    assert registry.get("provider-a") is new_provider_a

    # Unregister
    removed = registry.unregister("provider-a")
    assert removed is new_provider_a
    assert registry.get("provider-a") is None
    assert len(registry.list_providers()) == 0


def test_registry_enabled_filtering():
    registry = ProviderRegistry()
    p1 = MockProvider(provider_id="p1", enabled=True)
    p2 = MockProvider(provider_id="p2", enabled=False)

    registry.register(p1)
    registry.register(p2)

    assert len(registry.list_providers(enabled_only=False)) == 2
    assert len(registry.list_providers(enabled_only=True)) == 1
    assert registry.list_providers(enabled_only=True)[0].provider_id == "p1"


def test_registry_model_aggregation_and_health_checks():
    async def _test():
        registry = ProviderRegistry()
        p1 = MockProvider(
            provider_id="p1",
            models=[
                Model(
                    provider="p1",
                    model_id="m1-fast",
                    display_name="M1 Fast",
                    capabilities={ModelCapability.FAST},
                    context_window=32000,
                )
            ],
        )
        p2 = MockProvider(
            provider_id="p2",
            models=[
                Model(
                    provider="p2",
                    model_id="m2-coding",
                    display_name="M2 Coding",
                    capabilities={ModelCapability.CODING},
                    context_window=64000,
                )
            ],
        )

        registry.register(p1)
        registry.register(p2)

        # Aggregated models
        models = await registry.list_all_models()
        model_ids = {m.model_id for m in models}
        assert model_ids == {"m1-fast", "m2-coding"}

        # Health checks
        health_map = await registry.check_health_all()
        assert "p1" in health_map
        assert "p2" in health_map
        assert health_map["p1"].is_healthy is True
        assert health_map["p2"].is_healthy is True

    asyncio.run(_test())


def test_acceptance_criteria_add_provider_c():
    """Phase 2 Acceptance: เพิ่ม Provider C ได้โดยไม่ต้องแก้ routing/registry logic."""
    async def _test():
        registry = ProviderRegistry()

        # Existing providers in the system
        registry.register(MockProvider(provider_id="provider-a"))
        registry.register(MockProvider(provider_id="provider-b"))
        assert len(registry.list_providers()) == 2

        # Dynamically add Provider C
        provider_c = MockProvider(
            provider_id="provider-c",
            models=[
                Model(
                    provider="provider-c",
                    model_id="model-c-reasoning",
                    display_name="Model C Reasoning",
                    capabilities={ModelCapability.REASONING},
                    context_window=100000,
                )
            ],
            default_response="Response from Provider C",
        )
        registry.register(provider_c)

        # Verify Provider C is immediately available without any architectural modification
        assert registry.get("provider-c") is provider_c
        assert len(registry.list_providers()) == 3

        all_models = await registry.list_all_models()
        c_models = [m for m in all_models if m.provider == "provider-c"]
        assert len(c_models) == 1
        assert c_models[0].model_id == "model-c-reasoning"

        # Verify health check includes Provider C
        health_map = await registry.check_health_all()
        assert "provider-c" in health_map
        assert health_map["provider-c"].is_healthy is True

    asyncio.run(_test())
