"""AI message, request, and response representations."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from lamaru.domain.errors import ErrorCategory
from lamaru.domain.tools import ToolDefinition


class MessageRole(str, Enum):
    """Role of a message author."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """A tool call requested by a model."""

    id: str = Field(..., description="Unique ID for this tool invocation")
    name: str = Field(..., description="Name of the tool to invoke")
    arguments: Dict[str, Any] = Field(
        default_factory=dict, description="Parsed input arguments for the tool"
    )


class Message(BaseModel):
    """A single message within a conversation or request."""

    role: MessageRole = Field(..., description="Author role")
    content: Optional[str] = Field(default=None, description="Text content")
    name: Optional[str] = Field(default=None, description="Optional name of author/tool")
    tool_call_id: Optional[str] = Field(
        default=None, description="Tool call ID this message responds to (for role='tool')"
    )
    tool_calls: List[ToolCall] = Field(
        default_factory=list, description="List of tool calls requested by assistant"
    )


class Usage(BaseModel):
    """Token usage metrics."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class FinishReason(str, Enum):
    """Reason for completion."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class AIRequest(BaseModel):
    """Normalized request sent to an AI provider."""

    messages: List[Message] = Field(..., description="Conversation messages")
    model: str = Field(..., description="Target model identifier")
    provider: Optional[str] = Field(default=None, description="Target provider name")
    key_id: Optional[str] = Field(default=None, description="Internal key identifier to use")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, gt=0)
    tools: List[ToolDefinition] = Field(
        default_factory=list, description="Tools available for the model to call"
    )
    stream: bool = Field(default=False, description="Whether to stream response")
    system_prompt: Optional[str] = Field(
        default=None, description="Optional overriding system prompt"
    )


class AIResponse(BaseModel):
    """Normalized response returned from an AI provider."""

    provider: str = Field(..., description="Provider that generated the response")
    model: str = Field(..., description="Model that generated the response")
    content: Optional[str] = Field(default=None, description="Generated text content")
    tool_calls: List[ToolCall] = Field(
        default_factory=list, description="Tool calls requested by the model"
    )
    finish_reason: FinishReason = Field(default=FinishReason.STOP)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(default=0.0, ge=0.0)
    raw_error_category: Optional[ErrorCategory] = Field(default=None)


class AIStreamChunk(BaseModel):
    """A streaming chunk from an AI provider."""

    content_delta: str = Field(default="")
    tool_calls: List[ToolCall] = Field(default_factory=list)
    finish_reason: Optional[FinishReason] = Field(default=None)
    usage: Optional[Usage] = Field(default=None)
