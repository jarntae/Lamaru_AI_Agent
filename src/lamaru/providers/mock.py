"""In-memory MockProvider for testing, simulation, and local development."""

from typing import AsyncIterator, Dict, List, Optional
from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import (
    AIRequest,
    AIResponse,
    AIStreamChunk,
    FinishReason,
    Usage,
)
from lamaru.domain.models import Model, ModelCapability
from lamaru.domain.provider import AIProvider, ProviderHealth


class MockProvider(AIProvider):
    """A configurable mock provider that simulates generation, streaming, errors, and health checks."""

    def __init__(
        self,
        provider_id: str = "mock",
        enabled: bool = True,
        models: Optional[List[Model]] = None,
        default_response: str = "Mock generation response",
        latency_ms: float = 1.0,
    ) -> None:
        self._provider_id = provider_id
        self._enabled = enabled
        self._default_response = default_response
        self._latency_ms = latency_ms
        self._simulated_error: Optional[LamaruError] = None
        self._health_status: str = "healthy"
        self._call_count: int = 0

        self._models: Dict[str, Model] = {}
        if models:
            for m in models:
                self._models[m.model_id] = m
        else:
            default_model = Model(
                provider=self._provider_id,
                model_id="mock-default",
                display_name="Mock Default Model",
                capabilities={ModelCapability.CODING, ModelCapability.FAST},
                context_window=128000,
            )
            self._models[default_model.model_id] = default_model

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def call_count(self) -> int:
        return self._call_count

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def add_model(self, model: Model) -> None:
        self._models[model.model_id] = model

    def set_simulated_error(self, error: Optional[LamaruError]) -> None:
        """Inject a simulated error to be raised on subsequent generate/stream calls."""
        self._simulated_error = error

    def clear_simulated_error(self) -> None:
        self._simulated_error = None

    def set_health_status(self, status: str) -> None:
        self._health_status = status

    async def list_models(self) -> List[Model]:
        return list(self._models.values())

    async def get_model(self, model_id: str) -> Optional[Model]:
        return self._models.get(model_id)

    async def generate(self, request: AIRequest) -> AIResponse:
        self._call_count += 1
        if self._simulated_error is not None:
            raise self._simulated_error

        if request.model not in self._models:
            raise LamaruError(
                message=f"Model '{request.model}' not found in provider '{self.provider_id}'",
                category=ErrorCategory.MODEL_NOT_FOUND,
                provider=self.provider_id,
                model=request.model,
            )

        content = self._default_response
        input_tokens = sum(len(m.content or "") for m in request.messages) // 4
        output_tokens = len(content) // 4

        return AIResponse(
            provider=self.provider_id,
            model=request.model,
            content=content,
            finish_reason=FinishReason.STOP,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            latency_ms=self._latency_ms,
        )

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        self._call_count += 1
        if self._simulated_error is not None:
            raise self._simulated_error

        if request.model not in self._models:
            raise LamaruError(
                message=f"Model '{request.model}' not found in provider '{self.provider_id}'",
                category=ErrorCategory.MODEL_NOT_FOUND,
                provider=self.provider_id,
                model=request.model,
            )

        words = self._default_response.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else f" {word}"
            yield AIStreamChunk(content_delta=chunk)

        yield AIStreamChunk(
            finish_reason=FinishReason.STOP,
            usage=Usage(
                input_tokens=10,
                output_tokens=len(words) * 2,
                total_tokens=10 + len(words) * 2,
            ),
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider_id,
            status=self._health_status,
            latency_ms=self._latency_ms,
            message=f"Mock provider status: {self._health_status}",
        )
