import asyncio
import json
import pytest
import httpx

from lamaru.domain.models import Model, ModelCapability
from lamaru.gateway.app import create_app
from lamaru.models.registry import ModelRegistry
from lamaru.providers.mock import MockProvider
from lamaru.providers.registry import ProviderRegistry
from lamaru.router.fallback import FallbackEngine
from lamaru.router.router import ModelRouter


def build_test_app():
    model_reg = ModelRegistry()
    provider_reg = ProviderRegistry()

    provider = MockProvider(provider_id="mock", default_response="Hello from Lamaru Gateway!")
    provider_reg.register(provider)

    model = Model(
        provider="mock",
        model_id="mock-default",
        display_name="Mock Default Model",
        capabilities={ModelCapability.CODING, ModelCapability.FAST, ModelCapability.TOOL_CALLING},
        context_window=32000,
        priority=10,
    )
    model_reg.register(model)

    router = ModelRouter(model_registry=model_reg, provider_registry=provider_reg)
    fallback_engine = FallbackEngine(router=router, provider_registry=provider_reg)

    return create_app(
        fallback_engine=fallback_engine,
        model_registry=model_reg,
        provider_registry=provider_reg,
    )


def test_gateway_health():
    async def _test():
        app = build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/health")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["service"] == "lamaru-ai-gateway"
            assert "x-request-id" in res.headers

    asyncio.run(_test())


def test_gateway_v1_models():
    async def _test():
        app = build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/v1/models")
            assert res.status_code == 200
            data = res.json()
            assert data["object"] == "list"
            assert len(data["data"]) == 1
            assert data["data"][0]["id"] == "mock-default"
            assert data["data"][0]["owned_by"] == "mock"

    asyncio.run(_test())


def test_gateway_chat_completions():
    async def _test():
        app = build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "mock-default",
                "messages": [
                    {"role": "user", "content": "Hello!"}
                ],
                "temperature": 0.5,
            }
            res = await client.post("/v1/chat/completions", json=payload)
            assert res.status_code == 200
            data = res.json()
            assert data["object"] == "chat.completion"
            assert data["model"] == "mock-default"
            assert len(data["choices"]) == 1
            assert data["choices"][0]["message"]["content"] == "Hello from Lamaru Gateway!"
            assert data["usage"]["total_tokens"] > 0
            assert "x-request-id" in res.headers

    asyncio.run(_test())


def test_gateway_chat_completions_streaming():
    async def _test():
        app = build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "mock-default",
                "messages": [{"role": "user", "content": "Stream please"}],
                "stream": True,
            }
            res = await client.post("/v1/chat/completions", json=payload)
            assert res.status_code == 200
            assert "text/event-stream" in res.headers.get("content-type", "")

            body = res.text
            assert "data: " in body
            assert "data: [DONE]" in body

    asyncio.run(_test())


def test_gateway_agent_run():
    async def _test():
        app = build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"task": "Write unit tests for gateway"}
            res = await client.post("/v1/agent/run", json=payload)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "COMPLETED"
            assert "session-" in data["session_id"]
            assert data["task"] == "Write unit tests for gateway"

    asyncio.run(_test())


def test_gateway_agent_stream():
    async def _test():
        app = build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"task": "Stream agent run"}
            res = await client.post("/v1/agent/stream", json=payload)
            assert res.status_code == 200
            assert "text/event-stream" in res.headers.get("content-type", "")
            body = res.text
            assert "event: started" in body
            assert "event: completed" in body

    asyncio.run(_test())
