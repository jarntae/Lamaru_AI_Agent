"""Abstract interface contract for all AI providers."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional
from pydantic import BaseModel, Field

from lamaru.domain.messages import AIRequest, AIResponse, AIStreamChunk
from lamaru.domain.models import Model


class ProviderHealth(BaseModel):
    """Health check outcome for a provider."""

    provider: str = Field(..., description="Provider name")
    status: str = Field(
        default="healthy", description="Status e.g. healthy, degraded, unhealthy"
    )
    latency_ms: float = Field(default=0.0, ge=0.0)
    message: Optional[str] = Field(default=None)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"


class AIProvider(ABC):
    """Abstract interface defining required behavior for any AI Provider adapter."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique provider identifier (e.g. 'google', 'local')."""
        pass

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Whether this provider is currently enabled."""
        pass

    @abstractmethod
    async def list_models(self) -> List[Model]:
        """Discover and return normalized models supported by this provider."""
        pass

    @abstractmethod
    async def get_model(self, model_id: str) -> Optional[Model]:
        """Fetch normalized metadata for a specific model."""
        pass

    @abstractmethod
    async def generate(self, request: AIRequest) -> AIResponse:
        """Generate a complete completion response for an AI request."""
        pass

    @abstractmethod
    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        """Stream completion response chunks for an AI request."""
        pass

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Execute a connectivity and health check against the provider."""
        pass
