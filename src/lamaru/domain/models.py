"""Model representations and capability definitions for Lamaru AI Gateway."""

from enum import Enum
from typing import Optional, Set
from pydantic import BaseModel, Field


class ModelCapability(str, Enum):
    """Capabilities supported by models."""

    CODING = "coding"
    REASONING = "reasoning"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    RESEARCH = "research"
    FAST = "fast"


class Model(BaseModel):
    """Normalized model representation across all providers."""

    provider: str = Field(..., description="Provider name e.g. google, local")
    model_id: str = Field(..., description="Unique model identifier within provider")
    display_name: str = Field(..., description="Human-readable model name")
    capabilities: Set[ModelCapability] = Field(
        default_factory=set, description="Set of capabilities supported by the model"
    )
    context_window: int = Field(
        ..., gt=0, description="Maximum context window size in tokens"
    )
    priority: int = Field(
        default=0, description="Routing priority score (higher = preferred)"
    )
    stability: str = Field(
        default="stable", description="Stability tier e.g. stable, experimental, deprecated"
    )
    enabled: bool = Field(
        default=True, description="Whether this model is currently enabled for routing"
    )

    def has_capability(self, capability: ModelCapability) -> bool:
        """Check whether the model supports a specific capability."""
        return capability in self.capabilities


class TaskRequirement(BaseModel):
    """Requirements derived from a task for model selection."""

    task_type: str = Field(default="general_chat", description="Type of task e.g. coding, research")
    complexity: str = Field(default="medium", description="Complexity level: low, medium, high")
    requires_tools: bool = Field(default=False)
    requires_reasoning: bool = Field(default=False)
    requires_long_context: bool = Field(default=False)
    required_capabilities: Set[ModelCapability] = Field(default_factory=set)
