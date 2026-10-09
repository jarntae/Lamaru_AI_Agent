"""Request and response schemas for HTTP Gateway (OpenAI-compatible and Agent)."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ==========================================
# OpenAI-compatible Chat Completions
# ==========================================

class ChatMessagePayload(BaseModel):
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessagePayload]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None


class ChatChoice(BaseModel):
    index: int = 0
    message: Dict[str, Any]
    finish_reason: Optional[str] = "stop"


class ChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatChoice]
    usage: ChatUsage


# ==========================================
# OpenAI-compatible Models List
# ==========================================

class ModelItem(BaseModel):
    id: str
    object: str = "model"
    created: int = 1677610602
    owned_by: str = "lamaru"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: List[ModelItem]


# ==========================================
# Agent Endpoints
# ==========================================

class AgentRunRequest(BaseModel):
    task: str = Field(..., description="Task or instruction for the agent")
    workspace: str = Field(default=".", description="Workspace root directory path")
    model_policy: Optional[Dict[str, Any]] = None
    tool_policy: Optional[Dict[str, Any]] = None


class AgentRunResponse(BaseModel):
    session_id: str
    status: str
    task: str
    result: Optional[str] = None
    model_used: Optional[str] = None
    iterations: int = 0
