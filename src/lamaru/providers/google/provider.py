"""Google Gemini AI Provider implementation."""

import json
import logging
import os
import time
from typing import AsyncIterator, List, Optional
import httpx

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import AIRequest, AIResponse, AIStreamChunk, FinishReason
from lamaru.domain.models import Model
from lamaru.domain.provider import AIProvider, ProviderHealth
from lamaru.providers.google.errors import handle_network_error, parse_gemini_error
from lamaru.providers.google.mapper import (
    build_gemini_payload,
    parse_gemini_candidate,
    parse_gemini_model,
    parse_gemini_response,
)

logger = logging.getLogger(__name__)


class GoogleProvider(AIProvider):
    """Provider adapter for Google Gemini models via REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        key_id: str = "google-key-01",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        client: Optional[httpx.AsyncClient] = None,
        enabled: bool = True,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._key_id = key_id
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds

    @property
    def provider_id(self) -> str:
        return "google"

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-goog-api-key"] = self._api_key
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=self._timeout_seconds)

    async def list_models(self) -> List[Model]:
        """Fetch and return available Gemini models from API."""
        url = f"{self._base_url}/models"
        client = await self._get_client()

        try:
            response = await client.get(url, headers=self._get_headers())
            if response.status_code != 200:
                error_data = None
                try:
                    error_data = response.json()
                except Exception:
                    pass
                raise parse_gemini_error(
                    response.status_code, error_data, key_id=self._key_id
                )

            data = response.json()
            raw_models = data.get("models", [])
            models: List[Model] = []
            for m in raw_models:
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    models.append(parse_gemini_model(m))
            return models

        except httpx.RequestError as exc:
            raise handle_network_error(exc, key_id=self._key_id)
        finally:
            if self._client is None:
                await client.aclose()

    async def get_model(self, model_id: str) -> Optional[Model]:
        """Retrieve model metadata by model identifier."""
        models = await self.list_models()
        for m in models:
            if m.model_id == model_id:
                return m
        return None

    async def generate(self, request: AIRequest) -> AIResponse:
        """Send a generation request to Gemini generateContent."""
        start_time = time.perf_counter()
        # Clean model name if passed with 'models/' prefix
        clean_model = request.model.replace("models/", "")
        url = f"{self._base_url}/models/{clean_model}:generateContent"

        payload = build_gemini_payload(request)
        client = await self._get_client()

        try:
            response = await client.post(url, headers=self._get_headers(), json=payload)
            latency_ms = (time.perf_counter() - start_time) * 1000

            if response.status_code != 200:
                error_data = None
                try:
                    error_data = response.json()
                except Exception:
                    pass
                raise parse_gemini_error(
                    response.status_code,
                    error_data,
                    key_id=self._key_id,
                    model=clean_model,
                )

            data = response.json()
            return parse_gemini_response(data, model=clean_model, latency_ms=latency_ms)

        except httpx.RequestError as exc:
            raise handle_network_error(exc, key_id=self._key_id, model=clean_model)
        finally:
            if self._client is None:
                await client.aclose()

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        """Stream response chunks from Gemini streamGenerateContent using SSE."""
        clean_model = request.model.replace("models/", "")
        url = f"{self._base_url}/models/{clean_model}:streamGenerateContent?alt=sse"

        payload = build_gemini_payload(request)
        client = await self._get_client()

        try:
            async with client.stream(
                "POST", url, headers=self._get_headers(), json=payload
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_data = None
                    try:
                        error_data = json.loads(body.decode())
                    except Exception:
                        pass
                    raise parse_gemini_error(
                        response.status_code,
                        error_data,
                        key_id=self._key_id,
                        model=clean_model,
                    )

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue

                    json_str = line[len("data: ") :]
                    try:
                        chunk_data = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    candidates = chunk_data.get("candidates", [])
                    if candidates:
                        text, tool_calls, finish_reason = parse_gemini_candidate(
                            candidates[0], clean_model
                        )
                        yield AIStreamChunk(
                            content_delta=text or "",
                            tool_calls=tool_calls,
                            finish_reason=finish_reason if finish_reason != FinishReason.STOP else None,
                        )

            # Final finish stop chunk
            yield AIStreamChunk(finish_reason=FinishReason.STOP)

        except httpx.RequestError as exc:
            raise handle_network_error(exc, key_id=self._key_id, model=clean_model)
        finally:
            if self._client is None:
                await client.aclose()

    async def health_check(self) -> ProviderHealth:
        """Check provider connectivity by attempting a minimal models query."""
        if not self._api_key:
            return ProviderHealth(
                provider="google",
                status="unhealthy",
                latency_ms=0.0,
                message="No API key configured for Google provider",
            )

        start_time = time.perf_counter()
        try:
            await self.list_models()
            latency = (time.perf_counter() - start_time) * 1000
            return ProviderHealth(
                provider="google",
                status="healthy",
                latency_ms=latency,
                message="Google Gemini API reachable",
            )
        except LamaruError as err:
            latency = (time.perf_counter() - start_time) * 1000
            return ProviderHealth(
                provider="google",
                status="unhealthy",
                latency_ms=latency,
                message=f"Health check failed: {err.message} ({err.category.value})",
            )
        except Exception as exc:
            latency = (time.perf_counter() - start_time) * 1000
            return ProviderHealth(
                provider="google",
                status="unhealthy",
                latency_ms=latency,
                message=f"Health check failed with unexpected error: {exc}",
            )
