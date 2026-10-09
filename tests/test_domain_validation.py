import pytest
from pydantic import ValidationError

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.models import Model, ModelCapability, TaskRequirement
from lamaru.domain.messages import (
    AIRequest,
    AIResponse,
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
    Usage,
)
from lamaru.domain.tools import PermissionLevel, ToolDefinition, ToolResult


def test_model_validation_valid():
    model = Model(
        provider="google",
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        capabilities={ModelCapability.CODING, ModelCapability.FAST},
        context_window=1000000,
        priority=10,
    )
    assert model.provider == "google"
    assert model.has_capability(ModelCapability.CODING)
    assert not model.has_capability(ModelCapability.VISION)
    assert model.context_window == 1000000
    assert model.enabled is True


def test_model_validation_invalid_context_window():
    with pytest.raises(ValidationError):
        Model(
            provider="google",
            model_id="gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            context_window=0,  # context_window must be > 0
        )


def test_task_requirement_defaults():
    req = TaskRequirement(task_type="coding", requires_tools=True)
    assert req.task_type == "coding"
    assert req.requires_tools is True
    assert req.complexity == "medium"
    assert req.requires_reasoning is False


def test_message_and_tool_call():
    tool_call = ToolCall(id="call_1", name="read_file", arguments={"path": "README.md"})
    msg = Message(
        role=MessageRole.ASSISTANT,
        content="I will read the file.",
        tool_calls=[tool_call],
    )
    assert msg.role == MessageRole.ASSISTANT
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].name == "read_file"
    assert msg.tool_calls[0].arguments == {"path": "README.md"}


def test_ai_request_and_response_validation():
    req = AIRequest(
        messages=[Message(role=MessageRole.USER, content="Hello")],
        model="gemini-2.5-flash",
        temperature=0.5,
    )
    assert req.model == "gemini-2.5-flash"
    assert req.temperature == 0.5
    assert req.stream is False

    # Temperature out of bounds check
    with pytest.raises(ValidationError):
        AIRequest(
            messages=[Message(role=MessageRole.USER, content="Hello")],
            model="gemini-2.5-flash",
            temperature=3.5,  # le=2.0
        )

    res = AIResponse(
        provider="google",
        model="gemini-2.5-flash",
        content="World",
        finish_reason=FinishReason.STOP,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        latency_ms=150.0,
    )
    assert res.finish_reason == FinishReason.STOP
    assert res.usage.total_tokens == 15
    assert res.latency_ms == 150.0


def test_tool_definition_and_result():
    tool_def = ToolDefinition(
        name="read_file",
        description="Reads a file from workspace",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        permission=PermissionLevel.ALLOWED,
        timeout_seconds=15.0,
    )
    assert tool_def.permission == PermissionLevel.ALLOWED
    assert tool_def.timeout_seconds == 15.0

    result = ToolResult(
        success=True,
        output="file contents",
        metadata={"bytes_read": 13},
    )
    assert result.success is True
    assert result.output == "file contents"
    assert result.error is None
    assert result.metadata["bytes_read"] == 13


def test_error_categories_and_lamaru_error():
    assert ErrorCategory.RATE_LIMIT.is_retryable is True
    assert ErrorCategory.RATE_LIMIT.is_permanent is False
    assert ErrorCategory.AUTH_ERROR.is_retryable is False
    assert ErrorCategory.AUTH_ERROR.is_permanent is True

    err = LamaruError(
        message="Rate limit reached",
        category=ErrorCategory.RATE_LIMIT,
        provider="google",
        model="gemini-2.5-flash",
        key_id="google-key-01",
    )
    assert err.is_retryable is True
    assert err.is_permanent is False
    dict_repr = err.to_dict()
    assert dict_repr["category"] == "RATE_LIMIT"
    assert dict_repr["key_id"] == "google-key-01"
