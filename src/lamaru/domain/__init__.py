"""Domain models, types, and interfaces for Lamaru AI Gateway."""

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.models import Model, ModelCapability, TaskRequirement
from lamaru.domain.messages import (
    AIRequest,
    AIResponse,
    AIStreamChunk,
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
    Usage,
)
from lamaru.domain.tools import (
    PermissionLevel,
    Tool,
    ToolDefinition,
    ToolResult,
)
from lamaru.domain.agent import AgentState, AgentStatus, Plan
from lamaru.domain.provider import AIProvider, ProviderHealth

__all__ = [
    "ErrorCategory",
    "LamaruError",
    "Model",
    "ModelCapability",
    "TaskRequirement",
    "MessageRole",
    "ToolCall",
    "Message",
    "Usage",
    "FinishReason",
    "AIRequest",
    "AIResponse",
    "AIStreamChunk",
    "PermissionLevel",
    "ToolDefinition",
    "ToolResult",
    "Tool",
    "AgentStatus",
    "Plan",
    "AgentState",
    "AIProvider",
    "ProviderHealth",
]
