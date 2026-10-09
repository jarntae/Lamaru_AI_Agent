"""Unit tests for GoogleProvider using mocked HTTP transport (no real API calls)."""

import asyncio
import json
import pytest
import httpx

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import AIRequest, FinishReason, Message, MessageRole
from lamaru.domain.models import ModelCapability
from lamaru.domain.tools import ToolDefinition
from lamaru.providers.google.provider import GoogleProvider


def test_google_provider_list_models():
    async def _test():
        models_response_data = {
            "models": [
                {
                    "name": "models/gemini-2.5-flash",
                    "displayName": "Gemini 2.5 Flash",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 1048576,
                },
                {
                    "name": "models/gemini-2.5-pro",
                    "displayName": "Gemini 2.5 Pro",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 2097152,
                },
                {
                    "name": "models/embedding-001",
                    "displayName": "Embedding 001",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/models")
            assert request.headers.get("x-goog-api-key") == "fake-key"
            return httpx.Response(200, json=models_response_data)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            models = await provider.list_models()

            # Embedding model should be excluded since it doesn't support generateContent
            assert len(models) == 2

            flash_model = models[0]
            assert flash_model.model_id == "gemini-2.5-flash"
            assert flash_model.provider == "google"
            assert flash_model.has_capability(ModelCapability.FAST)
            assert flash_model.has_capability(ModelCapability.CODING)
            assert flash_model.has_capability(ModelCapability.LONG_CONTEXT)

            pro_model = models[1]
            assert pro_model.model_id == "gemini-2.5-pro"
            assert pro_model.has_capability(ModelCapability.REASONING)

    asyncio.run(_test())


def test_google_provider_generate_prompt():
    async def _test():
        gemini_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello from Google Gemini!"}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 8,
                "candidatesTokenCount": 6,
                "totalTokenCount": 14,
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert "gemini-2.5-flash:generateContent" in request.url.path
            return httpx.Response(200, json=gemini_response)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            req = AIRequest(
                messages=[Message(role=MessageRole.USER, content="Hello")],
                model="gemini-2.5-flash",
            )
            res = await provider.generate(req)

            assert res.provider == "google"
            assert res.model == "gemini-2.5-flash"
            assert res.content == "Hello from Google Gemini!"
            assert res.finish_reason == FinishReason.STOP
            assert res.usage.input_tokens == 8
            assert res.usage.output_tokens == 6
            assert res.usage.total_tokens == 14

    asyncio.run(_test())


def test_google_provider_generate_with_tool_call():
    async def _test():
        gemini_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "read_file",
                                    "args": {"path": "doc/README.md"},
                                }
                            }
                        ],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            assert "tools" in body
            return httpx.Response(200, json=gemini_response)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            tool_def = ToolDefinition(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            )
            req = AIRequest(
                messages=[Message(role=MessageRole.USER, content="Read README")],
                model="gemini-2.5-flash",
                tools=[tool_def],
            )
            res = await provider.generate(req)

            assert len(res.tool_calls) == 1
            assert res.tool_calls[0].name == "read_file"
            assert res.tool_calls[0].arguments == {"path": "doc/README.md"}
            assert res.finish_reason == FinishReason.TOOL_CALLS

    asyncio.run(_test())


def test_google_provider_streaming():
    async def _test():
        sse_lines = (
            'data: {"candidates": [{"content": {"parts": [{"text": "Part 1 "}]}}]}\n\n'
            'data: {"candidates": [{"content": {"parts": [{"text": "Part 2"}]}}]}\n\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=sse_lines.encode(),
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            req = AIRequest(
                messages=[Message(role=MessageRole.USER, content="Stream this")],
                model="gemini-2.5-flash",
                stream=True,
            )

            chunks = []
            async for chunk in provider.stream(req):
                if chunk.content_delta:
                    chunks.append(chunk.content_delta)

            assert "".join(chunks) == "Part 1 Part 2"

    asyncio.run(_test())


def test_google_provider_invalid_key_401():
    async def _test():
        error_payload = {
            "error": {
                "code": 401,
                "message": "API key not valid. Please pass a valid API key.",
                "status": "UNAUTHENTICATED",
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json=error_payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="bad-key", client=client)
            req = AIRequest(
                messages=[Message(role=MessageRole.USER, content="Hello")],
                model="gemini-2.5-flash",
            )
            with pytest.raises(LamaruError) as exc_info:
                await provider.generate(req)

            assert exc_info.value.category == ErrorCategory.AUTH_ERROR
            assert exc_info.value.is_permanent is True

    asyncio.run(_test())


def test_google_provider_invalid_model_404():
    async def _test():
        error_payload = {
            "error": {
                "code": 404,
                "message": "models/nonexistent is not found for API version v1beta",
                "status": "NOT_FOUND",
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json=error_payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            req = AIRequest(
                messages=[Message(role=MessageRole.USER, content="Hello")],
                model="nonexistent",
            )
            with pytest.raises(LamaruError) as exc_info:
                await provider.generate(req)

            assert exc_info.value.category == ErrorCategory.MODEL_NOT_FOUND
            assert exc_info.value.is_permanent is True

    asyncio.run(_test())


def test_google_provider_rate_limit_429():
    async def _test():
        error_payload = {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted (e.g. check quota).",
                "status": "RESOURCE_EXHAUSTED",
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json=error_payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            req = AIRequest(
                messages=[Message(role=MessageRole.USER, content="Hello")],
                model="gemini-2.5-flash",
            )
            with pytest.raises(LamaruError) as exc_info:
                await provider.generate(req)

            assert exc_info.value.category == ErrorCategory.RATE_LIMIT
            assert exc_info.value.is_retryable is True

    asyncio.run(_test())


def test_google_provider_safety_block():
    async def _test():
        safety_response = {
            "candidates": [
                {
                    "finishReason": "SAFETY",
                    "safetyRatings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "HIGH"}],
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=safety_response)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            req = AIRequest(
                messages=[Message(role=MessageRole.USER, content="Dangerous request")],
                model="gemini-2.5-flash",
            )
            with pytest.raises(LamaruError) as exc_info:
                await provider.generate(req)

            assert exc_info.value.category == ErrorCategory.SAFETY_BLOCK
            assert exc_info.value.is_permanent is True

    asyncio.run(_test())


def test_google_provider_health_check():
    async def _test():
        # Unhealthy when no key is set
        no_key_provider = GoogleProvider(api_key="")
        health_no_key = await no_key_provider.health_check()
        assert health_no_key.is_healthy is False

        # Healthy when API succeeds
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": []})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = GoogleProvider(api_key="fake-key", client=client)
            health = await provider.health_check()
            assert health.is_healthy is True
            assert health.provider == "google"

    asyncio.run(_test())
