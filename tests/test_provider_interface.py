import pytest
from typing import AsyncIterator, List, Optional

from lamaru.domain.messages import AIRequest, AIResponse, AIStreamChunk, Message, MessageRole
from lamaru.domain.models import Model, ModelCapability
from lamaru.domain.provider import AIProvider, ProviderHealth


class DummyProvider(AIProvider):
    """Concrete implementation of AIProvider for contract testing."""

    def __init__(self, provider_id: str = "dummy", enabled: bool = True) -> None:
        self._provider_id = provider_id
        self._enabled = enabled

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def list_models(self) -> List[Model]:
        return [
            Model(
                provider=self.provider_id,
                model_id="dummy-fast",
                display_name="Dummy Fast",
                capabilities={ModelCapability.FAST},
                context_window=32000,
            )
        ]

    async def get_model(self, model_id: str) -> Optional[Model]:
        if model_id == "dummy-fast":
            models = await self.list_models()
            return models[0]
        return None

    async def generate(self, request: AIRequest) -> AIResponse:
        return AIResponse(
            provider=self.provider_id,
            model=request.model,
            content="Dummy response",
        )

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        yield AIStreamChunk(content_delta="Dummy")
        yield AIStreamChunk(content_delta=" response")

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider_id,
            status="healthy",
            latency_ms=1.5,
        )


def test_cannot_instantiate_abstract_ai_provider():
    with pytest.raises(TypeError):
        AIProvider()  # type: ignore[abstract]


def test_concrete_provider_contract():
    async def _run_checks():
        provider = DummyProvider()
        assert provider.provider_id == "dummy"
        assert provider.is_enabled is True

        # Test list_models
        models = await provider.list_models()
        assert len(models) == 1
        assert models[0].model_id == "dummy-fast"

        # Test get_model
        model = await provider.get_model("dummy-fast")
        assert model is not None
        assert model.context_window == 32000

        not_found = await provider.get_model("unknown")
        assert not_found is None

        # Test generate
        req = AIRequest(
            messages=[Message(role=MessageRole.USER, content="ping")],
            model="dummy-fast",
        )
        res = await provider.generate(req)
        assert res.provider == "dummy"
        assert res.content == "Dummy response"

        # Test stream
        chunks = []
        async for chunk in provider.stream(req):
            chunks.append(chunk.content_delta)
        assert "".join(chunks) == "Dummy response"

        # Test health check
        health = await provider.health_check()
        assert health.is_healthy is True
        assert health.latency_ms == 1.5

    import asyncio
    asyncio.run(_run_checks())
