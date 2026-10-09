import asyncio
import pytest

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import AIRequest, FinishReason, Message, MessageRole
from lamaru.domain.models import ModelCapability
from lamaru.providers.mock import MockProvider


def test_mock_provider_generation_and_metadata():
    async def _test():
        provider = MockProvider(provider_id="mock-1", default_response="Hello, Lamaru!")
        assert provider.provider_id == "mock-1"
        assert provider.is_enabled is True
        assert provider.call_count == 0

        # Model inspection
        models = await provider.list_models()
        assert len(models) == 1
        assert models[0].model_id == "mock-default"
        assert models[0].has_capability(ModelCapability.CODING)

        req = AIRequest(
            messages=[Message(role=MessageRole.USER, content="Hello")],
            model="mock-default",
        )
        res = await provider.generate(req)
        assert res.provider == "mock-1"
        assert res.model == "mock-default"
        assert res.content == "Hello, Lamaru!"
        assert res.finish_reason == FinishReason.STOP
        assert res.usage.total_tokens > 0
        assert provider.call_count == 1

    asyncio.run(_test())


def test_mock_provider_streaming():
    async def _test():
        provider = MockProvider(default_response="Streamed test message")
        req = AIRequest(
            messages=[Message(role=MessageRole.USER, content="Hi")],
            model="mock-default",
            stream=True,
        )

        deltas = []
        async for chunk in provider.stream(req):
            if chunk.content_delta:
                deltas.append(chunk.content_delta)

        assert "".join(deltas) == "Streamed test message"
        assert provider.call_count == 1

    asyncio.run(_test())


def test_mock_provider_simulated_errors():
    async def _test():
        provider = MockProvider()

        # Unknown model error
        with pytest.raises(LamaruError) as exc_info:
            await provider.generate(
                AIRequest(
                    messages=[Message(role=MessageRole.USER, content="Hi")],
                    model="nonexistent-model",
                )
            )
        assert exc_info.value.category == ErrorCategory.MODEL_NOT_FOUND

        # Injected simulated error (e.g. RATE_LIMIT)
        rate_limit_err = LamaruError(
            message="Rate limit reached on mock",
            category=ErrorCategory.RATE_LIMIT,
            provider="mock",
        )
        provider.set_simulated_error(rate_limit_err)

        with pytest.raises(LamaruError) as exc_info:
            await provider.generate(
                AIRequest(
                    messages=[Message(role=MessageRole.USER, content="Hi")],
                    model="mock-default",
                )
            )
        assert exc_info.value.category == ErrorCategory.RATE_LIMIT
        assert exc_info.value.is_retryable is True

        # Clear error and verify recovery
        provider.clear_simulated_error()
        res = await provider.generate(
            AIRequest(
                messages=[Message(role=MessageRole.USER, content="Hi")],
                model="mock-default",
            )
        )
        assert res.content is not None

    asyncio.run(_test())


def test_mock_provider_health_check():
    async def _test():
        provider = MockProvider()
        health = await provider.health_check()
        assert health.is_healthy is True

        provider.set_health_status("degraded")
        health = await provider.health_check()
        assert health.status == "degraded"
        assert health.is_healthy is False

    asyncio.run(_test())
