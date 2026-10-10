"""Context tier models, budget tracking, and assembled context structures."""

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from lamaru.domain.messages import Message


class ContextTier(str, Enum):
    """Priority tiers for assembling context."""

    TIER_1_TASK = "TIER_1_TASK"                          # Current task and immediate constraints (Highest priority)
    TIER_2_FILES = "TIER_2_FILES"                        # Directly relevant files and code sections
    TIER_3_KNOWLEDGE = "TIER_3_KNOWLEDGE"                # Architecture, conventions, project rules
    TIER_4_RECENT_MESSAGES = "TIER_4_RECENT_MESSAGES"    # Recent conversation turns
    TIER_5_COMPRESSED_HISTORY = "TIER_5_COMPRESSED_HISTORY"  # Summarized older history


class ContextItem(BaseModel):
    """An individual piece of context to be ranked and budgeted."""

    tier: ContextTier
    source: str = Field(..., description="Origin of this context (e.g. file path, task, message_id)")
    content: str
    estimated_tokens: int = Field(default=0, ge=0)
    priority: int = Field(default=0, description="Priority score within tier (higher = more relevant)")


class ContextBudget(BaseModel):
    """Token budget tracking: input estimate, output budget, total context, model context limit."""

    model_context_limit: int = Field(..., gt=0, description="Maximum context window of the target model")
    output_budget: int = Field(default=4096, gt=0, description="Reserved tokens for model generation")
    max_input_budget: int = Field(..., gt=0, description="Upper bound allowed for input context")
    input_estimate: int = Field(default=0, ge=0, description="Actual estimated input tokens")
    total_context: int = Field(default=0, ge=0, description="input_estimate + output_budget")

    @property
    def is_within_limit(self) -> bool:
        return self.total_context <= self.model_context_limit

    @property
    def remaining_budget(self) -> int:
        return max(0, self.max_input_budget - self.input_estimate)


class AssembledContext(BaseModel):
    """Final assembled context payload prepared for model invocation."""

    budget: ContextBudget
    items: List[ContextItem] = Field(default_factory=list)
    system_instruction: str = ""
    messages: List[Message] = Field(default_factory=list)
    relevant_files: List[str] = Field(default_factory=list)
    is_compressed: bool = False
