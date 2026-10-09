"""Data transformation mapper between Lamaru domain objects and Google Gemini API payloads."""

import uuid
from typing import Any, Dict, List, Optional, Tuple

from lamaru.domain.errors import ErrorCategory, LamaruError
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
from lamaru.domain.models import Model, ModelCapability
from lamaru.domain.tools import ToolDefinition


def build_gemini_payload(request: AIRequest) -> Dict[str, Any]:
    """Convert a normalized AIRequest into a Gemini generateContent JSON payload."""
    contents: List[Dict[str, Any]] = []
    system_instruction: Optional[Dict[str, Any]] = None

    # Handle system prompt if explicitly set in request
    if request.system_prompt:
        system_instruction = {"parts": [{"text": request.system_prompt}]}

    for msg in request.messages:
        if msg.role == MessageRole.SYSTEM:
            # If system message is in conversation history, use it as systemInstruction
            system_instruction = {"parts": [{"text": msg.content or ""}]}
            continue

        parts: List[Dict[str, Any]] = []

        if msg.role == MessageRole.USER:
            parts.append({"text": msg.content or ""})
            contents.append({"role": "user", "parts": parts})

        elif msg.role == MessageRole.ASSISTANT:
            if msg.content:
                parts.append({"text": msg.content})
            for tc in msg.tool_calls:
                parts.append({
                    "functionCall": {
                        "name": tc.name,
                        "args": tc.arguments,
                    }
                })
            contents.append({"role": "model", "parts": parts})

        elif msg.role == MessageRole.TOOL:
            function_response = {
                "name": msg.name or "tool_result",
                "response": {"result": msg.content or ""},
            }
            parts.append({"functionResponse": function_response})
            contents.append({"role": "function", "parts": parts})

    payload: Dict[str, Any] = {"contents": contents}

    if system_instruction:
        payload["systemInstruction"] = system_instruction

    # Generation configuration
    generation_config: Dict[str, Any] = {
        "temperature": request.temperature,
    }
    if request.max_tokens:
        generation_config["maxOutputTokens"] = request.max_tokens

    payload["generationConfig"] = generation_config

    # Tools
    if request.tools:
        declarations: List[Dict[str, Any]] = []
        for t in request.tools:
            declarations.append({
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema if t.input_schema else {"type": "object"},
            })
        payload["tools"] = [{"functionDeclarations": declarations}]

    return payload


def parse_gemini_candidate(
    candidate: Dict[str, Any], model: str
) -> Tuple[Optional[str], List[ToolCall], FinishReason]:
    """Extract content, tool calls, and finish reason from a Gemini candidate object."""
    finish_reason_raw = candidate.get("finishReason", "STOP")

    if finish_reason_raw == "SAFETY":
        raise LamaruError(
            message="Response blocked by Google Gemini safety filters",
            category=ErrorCategory.SAFETY_BLOCK,
            provider="google",
            model=model,
            details=candidate.get("safetyRatings", {}),
        )

    finish_reason = FinishReason.STOP
    if finish_reason_raw == "MAX_TOKENS":
        finish_reason = FinishReason.LENGTH

    text_pieces: List[str] = []
    tool_calls: List[ToolCall] = []

    content_data = candidate.get("content", {})
    parts = content_data.get("parts", [])

    for part in parts:
        if "text" in part:
            text_pieces.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=fc.get("name", ""),
                    arguments=fc.get("args", {}),
                )
            )
            finish_reason = FinishReason.TOOL_CALLS

    final_text = "".join(text_pieces) if text_pieces else None
    return final_text, tool_calls, finish_reason


def parse_gemini_response(
    response_data: Dict[str, Any], model: str, latency_ms: float = 0.0
) -> AIResponse:
    """Parse complete Gemini generateContent JSON response into an AIResponse."""
    candidates = response_data.get("candidates", [])
    if not candidates:
        return AIResponse(
            provider="google",
            model=model,
            content="",
            finish_reason=FinishReason.STOP,
            latency_ms=latency_ms,
        )

    content, tool_calls, finish_reason = parse_gemini_candidate(candidates[0], model)

    usage_data = response_data.get("usageMetadata", {})
    input_tokens = usage_data.get("promptTokenCount", 0)
    output_tokens = usage_data.get("candidatesTokenCount", 0)
    total_tokens = usage_data.get("totalTokenCount", input_tokens + output_tokens)

    return AIResponse(
        provider="google",
        model=model,
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        ),
        latency_ms=latency_ms,
    )


def parse_gemini_model(model_data: Dict[str, Any]) -> Model:
    """Parse a single model descriptor from Gemini's listModels API."""
    raw_name = model_data.get("name", "")
    # Normalize "models/gemini-2.5-flash" -> "gemini-2.5-flash"
    model_id = raw_name.replace("models/", "")
    display_name = model_data.get("displayName", model_id)
    input_limit = model_data.get("inputTokenLimit", 32000)

    capabilities = {ModelCapability.CODING, ModelCapability.TOOL_CALLING}
    name_lower = model_id.lower()

    if "flash" in name_lower or "fast" in name_lower:
        capabilities.add(ModelCapability.FAST)
    if "pro" in name_lower:
        capabilities.add(ModelCapability.REASONING)
    if input_limit >= 100000:
        capabilities.add(ModelCapability.LONG_CONTEXT)

    return Model(
        provider="google",
        model_id=model_id,
        display_name=display_name,
        capabilities=capabilities,
        context_window=input_limit,
        priority=10 if "flash" in name_lower else 5,
        stability="stable",
        enabled=True,
    )
