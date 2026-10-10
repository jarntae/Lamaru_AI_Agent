"""Tests for AgentLoop subsystem (Phase 16)."""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from lamaru.agent.loop import AgentLoop, AgentLoopConfig, AgentLoopResult
from lamaru.agent.state_manager import AgentStateManager
from lamaru.context.manager import ContextManager
from lamaru.domain.agent import AgentState, AgentStatus, Plan
from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import (
    AIRequest,
    AIResponse,
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
    Usage,
)
from lamaru.domain.models import Model, ModelCapability
from lamaru.domain.tools import PermissionLevel, Tool, ToolDefinition, ToolResult
from lamaru.errors.classifier import ErrorClassifier
from lamaru.keys.manager import APIKeyManager
from lamaru.keys.models import APIKeyMetadata, SecretReference
from lamaru.models.registry import ModelRegistry
from lamaru.providers.mock import MockProvider
from lamaru.providers.registry import ProviderRegistry
from lamaru.resilience.cooldown import CooldownTracker
from lamaru.router.fallback import FallbackEngine
from lamaru.router.router import ModelRouter
from lamaru.tools.registry import ToolRegistry
from lamaru.tools.security import ToolSecurityPolicy


class ProgrammableMockProvider(MockProvider):
    """MockProvider allowing a programmed sequence of responses for multi-turn agent testing."""

    def __init__(
        self,
        provider_id: str = "mock-prog",
        responses: Optional[List[AIResponse]] = None,
        models: Optional[List[Model]] = None,
    ) -> None:
        super().__init__(provider_id=provider_id, models=models)
        self.responses_queue: List[AIResponse] = responses or []
        self.received_requests: List[AIRequest] = []

    async def generate(self, request: AIRequest) -> AIResponse:
        self._call_count += 1
        self.received_requests.append(request)

        if self._simulated_error is not None:
            raise self._simulated_error

        if self.responses_queue:
            return self.responses_queue.pop(0)

        return await super().generate(request)


class MockCalculatorTool(Tool):
    """Simple arithmetic tool for testing tool invocation in the loop."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="calculate",
            description="Perform simple arithmetic calculation",
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["operation", "a", "b"],
            },
            timeout_seconds=5.0,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        op = input_args.get("operation")
        a = input_args.get("a", 0)
        b = input_args.get("b", 0)
        if op == "add":
            return ToolResult(success=True, output=a + b)
        elif op == "multiply":
            return ToolResult(success=True, output=a * b)
        else:
            return ToolResult(success=False, error=f"Unknown operation '{op}'")


class SlowTool(Tool):
    """Tool that sleeps to simulate timeout."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="slow_tool",
            description="A tool that takes a long time",
            timeout_seconds=0.2,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        await asyncio.sleep(1.0)
        return ToolResult(success=True, output="Done")


def setup_test_environment(
    provider: Optional[MockProvider] = None,
    security_policy: Optional[ToolSecurityPolicy] = None,
):
    """Setup standard test environment with ModelRegistry, ProviderRegistry, KeyManager, and FallbackEngine."""
    model_reg = ModelRegistry()
    provider_reg = ProviderRegistry()
    key_mgr = APIKeyManager()
    cooldown = CooldownTracker()
    error_classifier = ErrorClassifier()

    prov = provider or MockProvider(provider_id="mock", default_response="I have analyzed the request.")
    provider_reg.register(prov)

    model = Model(
        provider=prov.provider_id,
        model_id="test-model-1",
        display_name="Test Model 1",
        capabilities={ModelCapability.CODING, ModelCapability.FAST, ModelCapability.TOOL_CALLING},
        context_window=64000,
        priority=10,
    )
    model_reg.register(model)
    prov.add_model(model)

    key_mgr.register_key(
        APIKeyMetadata(
            key_id="k-01",
            provider=prov.provider_id,
            secret_ref=SecretReference.from_raw("test-secret-key"),
        )
    )

    router = ModelRouter(
        model_registry=model_reg,
        provider_registry=provider_reg,
        key_manager=key_mgr,
        cooldown_tracker=cooldown,
    )

    fallback_engine = FallbackEngine(
        router=router,
        provider_registry=provider_reg,
        key_manager=key_mgr,
        cooldown_tracker=cooldown,
        error_classifier=error_classifier,
    )

    state_mgr = AgentStateManager()
    context_mgr = ContextManager()

    tool_reg = ToolRegistry(security_policy=security_policy)
    tool_reg.register(MockCalculatorTool())

    loop = AgentLoop(
        state_manager=state_mgr,
        context_manager=context_mgr,
        fallback_engine=fallback_engine,
        tool_registry=tool_reg,
    )

    return loop, state_mgr, context_mgr, fallback_engine, tool_reg, prov


def test_agent_loop_basic_completion():
    """Verify standard single-turn completion without tool calls: CREATED -> PLANNING -> RUNNING -> COMPLETED."""
    async def _test():
        loop, state_mgr, _, _, _, _ = setup_test_environment()

        result = await loop.create_and_run(task="Explain recursion in computer science")

        assert result.status == AgentStatus.COMPLETED
        assert result.cancelled is False
        assert result.timed_out is False
        assert result.error is None
        assert result.iterations == 1
        assert result.final_response == "I have analyzed the request."
        assert "test-model-1" in result.models_used

        # Check persisted state
        state = state_mgr.get_session(result.session_id)
        assert state is not None
        assert state.status == AgentStatus.COMPLETED
        assert state.plan.steps[0].startswith("Analyze task:")
        assert state.current_step == "Completed"
        assert len(state.messages) == 2  # [USER, ASSISTANT]
        assert state.messages[0].role == MessageRole.USER
        assert state.messages[1].role == MessageRole.ASSISTANT
        assert state.messages[1].content == "I have analyzed the request."
        assert state.model_history == ["test-model-1"]

    asyncio.run(_test())


def test_agent_loop_tool_execution_flow():
    """Verify multi-step ReAct tool loop:
    CREATED -> PLANNING -> RUNNING -> WAITING_TOOL -> RUNNING -> COMPLETED."""
    async def _test():
        # First turn: model requests tool call
        turn_1_resp = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="I will compute 15 + 27 first.",
            tool_calls=[
                ToolCall(
                    id="call_calc_1",
                    name="calculate",
                    arguments={"operation": "add", "a": 15, "b": 27},
                )
            ],
            finish_reason=FinishReason.TOOL_CALLS,
        )
        # Second turn: model provides final answer
        turn_2_resp = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="The sum is 42.",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
        )

        prog_provider = ProgrammableMockProvider(
            provider_id="mock-prog",
            responses=[turn_1_resp, turn_2_resp],
        )

        loop, state_mgr, _, _, tool_reg, _ = setup_test_environment(provider=prog_provider)

        events: List[Dict[str, Any]] = []
        def listener(ev: Dict[str, Any]):
            events.append(ev)

        result = await loop.create_and_run(
            task="Calculate 15 + 27",
            on_event=listener,
        )

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 2
        assert result.final_response == "The sum is 42."
        assert result.tools_invoked == ["calculate"]
        assert result.tool_results_count == 1

        # Check state messages: USER -> ASSISTANT(tool_calls) -> TOOL(result) -> ASSISTANT(final)
        state = state_mgr.get_session(result.session_id)
        assert state is not None
        assert len(state.messages) == 4
        assert state.messages[0].role == MessageRole.USER
        assert state.messages[1].role == MessageRole.ASSISTANT
        assert state.messages[1].tool_calls[0].name == "calculate"
        assert state.messages[2].role == MessageRole.TOOL
        assert state.messages[2].tool_call_id == "call_calc_1"
        assert state.messages[2].content == "42"
        assert state.messages[3].role == MessageRole.ASSISTANT
        assert state.messages[3].content == "The sum is 42."

        # Check tool history and results
        assert len(state.tool_history) == 1
        assert state.tool_history[0]["name"] == "calculate"
        assert len(state.tool_results) == 1
        assert state.tool_results[0].success is True
        assert state.tool_results[0].output == 42

        # Verify emitted events
        event_names = [e["event"] for e in events]
        assert "planning" in event_names
        assert "started" in event_names
        assert "iteration_start" in event_names
        assert "tool_start" in event_names
        assert "tool_end" in event_names
        assert "completed" in event_names

    asyncio.run(_test())


def test_agent_loop_max_iterations_enforcement():
    """Verify loop terminates with FAILED status when exceeding max_iterations."""
    async def _test():
        # Infinitely request tools
        class EndlessToolProvider(MockProvider):
            async def generate(self, request: AIRequest) -> AIResponse:
                return AIResponse(
                    provider=self.provider_id,
                    model="test-model-1",
                    content="Looping...",
                    tool_calls=[
                        ToolCall(
                            id=f"call_{self._call_count}",
                            name="calculate",
                            arguments={"operation": "add", "a": 1, "b": 1},
                        )
                    ],
                )

        provider = EndlessToolProvider(provider_id="mock-endless")
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=provider)

        cfg = AgentLoopConfig(max_iterations=3)
        result = await loop.create_and_run(task="Endless task", config=cfg)

        assert result.status == AgentStatus.FAILED
        assert result.iterations == 3
        assert "Maximum iterations (3) reached" in (result.error or "")

        state = state_mgr.get_session(result.session_id)
        assert state.status == AgentStatus.FAILED
        assert any("Maximum iterations" in err for err in state.errors)

    asyncio.run(_test())


def test_agent_loop_timeout_protection():
    """Verify overall run timeout terminates execution gracefully."""
    async def _test():
        class SleepingProvider(MockProvider):
            async def generate(self, request: AIRequest) -> AIResponse:
                await asyncio.sleep(0.3)
                return AIResponse(
                    provider=self.provider_id,
                    model="test-model-1",
                    content="Too late",
                )

        provider = SleepingProvider(provider_id="mock-sleep")
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=provider)

        cfg = AgentLoopConfig(timeout_seconds=0.1)
        result = await loop.create_and_run(task="Sleep task", config=cfg)

        assert result.status == AgentStatus.FAILED
        assert result.timed_out is True
        assert "timed out after 0.1 seconds" in (result.error or "")

        state = state_mgr.get_session(result.session_id)
        assert state.status == AgentStatus.FAILED

    asyncio.run(_test())


def test_agent_loop_cooperative_cancellation():
    """Verify cooperative cancellation mechanism via cancel(session_id)."""
    async def _test():
        loop, state_mgr, _, _, _, _ = setup_test_environment()

        session = state_mgr.create_session("Long running operation")
        # Request cancel before run
        loop.cancel(session.session_id)

        result = await loop.run(session.session_id)

        assert result.status == AgentStatus.CANCELLED
        assert result.cancelled is True

        state = state_mgr.get_session(session.session_id)
        assert state.status == AgentStatus.CANCELLED
        assert state.status.is_terminal is True

    asyncio.run(_test())


def test_agent_loop_cancellation_with_event_token():
    """Verify cooperative cancellation via cancellation_token Event."""
    async def _test():
        loop, state_mgr, _, _, _, _ = setup_test_environment()
        token = asyncio.Event()
        token.set()  # Cancel immediately

        result = await loop.create_and_run("Cancel via token", cancellation_token=token)

        assert result.status == AgentStatus.CANCELLED
        assert result.cancelled is True

    asyncio.run(_test())


def test_agent_loop_tool_timeout_delegation():
    """Verify per-tool timeout protection handled by ToolRegistry without loop crashing."""
    async def _test():
        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Calling slow tool...",
            tool_calls=[ToolCall(id="c_slow", name="slow_tool", arguments={})],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Tool timed out, handling failure gracefully.",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(
            provider_id="mock-prog",
            responses=[turn_1, turn_2],
        )

        loop, state_mgr, _, _, tool_reg, _ = setup_test_environment(provider=prog_prov)
        tool_reg.register(SlowTool())

        result = await loop.create_and_run("Run slow tool")

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 2
        # Check tool result has timeout error
        state = state_mgr.get_session(result.session_id)
        assert len(state.tool_results) == 1
        assert state.tool_results[0].success is False
        assert "timed out after 0.2 seconds" in state.tool_results[0].error

    asyncio.run(_test())


def test_agent_loop_model_failure_recovery():
    """Verify FallbackEngine automatically recovers when a candidate model fails (e.g. Rate Limit 429)."""
    async def _test():
        model_reg = ModelRegistry()
        provider_reg = ProviderRegistry()
        key_mgr = APIKeyManager()
        cooldown = CooldownTracker()
        error_classifier = ErrorClassifier()

        # Provider A with key-a (will fail with 429)
        prov_a = MockProvider(provider_id="provider-a")
        prov_a.set_simulated_error(
            LamaruError(
                message="Resource exhausted: 429 Too Many Requests",
                category=ErrorCategory.RATE_LIMIT,
                provider="provider-a",
            )
        )
        provider_reg.register(prov_a)

        model_a = Model(
            provider="provider-a",
            model_id="model-a",
            display_name="Model A",
            capabilities={ModelCapability.CODING, ModelCapability.FAST},
            context_window=64000,
            priority=10,  # Preferred
        )
        model_reg.register(model_a)
        prov_a.add_model(model_a)
        key_mgr.register_key(
            APIKeyMetadata(key_id="k-a", provider="provider-a", secret_ref=SecretReference.from_raw("ka"))
        )

        # Provider B (Healthy fallback)
        prov_b = MockProvider(provider_id="provider-b", default_response="Recovered successfully via Model B!")
        provider_reg.register(prov_b)

        model_b = Model(
            provider="provider-b",
            model_id="model-b",
            display_name="Model B",
            capabilities={ModelCapability.CODING, ModelCapability.FAST},
            context_window=64000,
            priority=5,
        )
        model_reg.register(model_b)
        prov_b.add_model(model_b)
        key_mgr.register_key(
            APIKeyMetadata(key_id="k-b", provider="provider-b", secret_ref=SecretReference.from_raw("kb"))
        )

        router = ModelRouter(
            model_registry=model_reg,
            provider_registry=provider_reg,
            key_manager=key_mgr,
            cooldown_tracker=cooldown,
        )
        fallback_engine = FallbackEngine(
            router=router,
            provider_registry=provider_reg,
            key_manager=key_mgr,
            cooldown_tracker=cooldown,
            error_classifier=error_classifier,
        )

        state_mgr = AgentStateManager()
        context_mgr = ContextManager()

        loop = AgentLoop(
            state_manager=state_mgr,
            context_manager=context_mgr,
            fallback_engine=fallback_engine,
        )

        result = await loop.create_and_run("Test automatic model failover")

        assert result.status == AgentStatus.COMPLETED
        assert result.final_response == "Recovered successfully via Model B!"
        # State records the successful fallback model
        assert "model-b" in result.models_used
        state = state_mgr.get_session(result.session_id)
        assert state.model_history == ["model-b"]

    asyncio.run(_test())


def test_agent_loop_without_auto_plan():
    """Verify loop works directly from CREATED -> RUNNING when auto_plan=False."""
    async def _test():
        loop, state_mgr, _, _, _, _ = setup_test_environment()

        cfg = AgentLoopConfig(auto_plan=False)
        result = await loop.create_and_run("Direct execution", config=cfg)

        assert result.status == AgentStatus.COMPLETED
        state = state_mgr.get_session(result.session_id)
        assert state.status == AgentStatus.COMPLETED

    asyncio.run(_test())


def test_agent_loop_disk_persistence(tmp_path: Path):
    """Verify that AgentLoop persists state to disk across transitions and tool calls."""
    async def _test():
        storage_dir = tmp_path / "agent_sessions"
        state_mgr = AgentStateManager(storage_dir=storage_dir)

        # Provider with tool call and final response
        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Calculate 2 * 3",
            tool_calls=[
                ToolCall(
                    id="c_mul",
                    name="calculate",
                    arguments={"operation": "multiply", "a": 2, "b": 3},
                )
            ],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="2 * 3 = 6",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(
            provider_id="mock-prog",
            responses=[turn_1, turn_2],
        )

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        prov_reg.register(prog_prov)

        model = Model(
            provider="mock-prog",
            model_id="test-model-1",
            display_name="Test Model",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
        )
        model_reg.register(model)
        prog_prov.add_model(model)

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg)
        tool_reg = ToolRegistry()
        tool_reg.register(MockCalculatorTool())

        loop = AgentLoop(
            state_manager=state_mgr,
            context_manager=ContextManager(),
            router=router,
            provider_registry=prov_reg,
            tool_registry=tool_reg,
        )

        result = await loop.create_and_run("Multiply numbers")
        assert result.status == AgentStatus.COMPLETED

        # Check disk file exists and is valid
        persisted_file = storage_dir / f"{result.session_id}.json"
        assert persisted_file.exists()

        # Fresh manager loads from disk
        mgr2 = AgentStateManager(storage_dir=storage_dir)
        loaded = mgr2.get_session(result.session_id)
        assert loaded is not None
        assert loaded.status == AgentStatus.COMPLETED
        assert len(loaded.tool_results) == 1
        assert loaded.tool_results[0].output == 6
        assert loaded.messages[-1].content == "2 * 3 = 6"

    asyncio.run(_test())


def test_agent_loop_multistep_tool_execution():
    """Verify multi-step tool calls across multiple iterations until task completion."""
    async def _test():
        turn_1_resp = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="First, I'll calculate 10 + 20.",
            tool_calls=[
                ToolCall(
                    id="call_add",
                    name="calculate",
                    arguments={"operation": "add", "a": 10, "b": 20},
                )
            ],
            finish_reason=FinishReason.TOOL_CALLS,
        )
        turn_2_resp = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Now I'll multiply that result by 2.",
            tool_calls=[
                ToolCall(
                    id="call_mul",
                    name="calculate",
                    arguments={"operation": "multiply", "a": 30, "b": 2},
                )
            ],
            finish_reason=FinishReason.TOOL_CALLS,
        )
        turn_3_resp = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="The final result is 60.",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
        )

        prog_prov = ProgrammableMockProvider(
            provider_id="mock-prog",
            responses=[turn_1_resp, turn_2_resp, turn_3_resp],
        )

        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=prog_prov)

        result = await loop.create_and_run(task="Calculate (10 + 20) * 2")

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 3
        assert result.final_response == "The final result is 60."
        assert result.tools_invoked == ["calculate", "calculate"]
        assert result.tool_results_count == 2

        state = state_mgr.get_session(result.session_id)
        assert state is not None
        assert state.status == AgentStatus.COMPLETED
        # Expected messages: USER -> ASSISTANT (add) -> TOOL (30) -> ASSISTANT (mul) -> TOOL (60) -> ASSISTANT (final)
        assert len(state.messages) == 6
        assert state.messages[0].role == MessageRole.USER
        assert state.messages[1].role == MessageRole.ASSISTANT
        assert state.messages[1].tool_calls[0].name == "calculate"
        assert state.messages[2].role == MessageRole.TOOL
        assert state.messages[2].content == "30"
        assert state.messages[3].role == MessageRole.ASSISTANT
        assert state.messages[3].tool_calls[0].name == "calculate"
        assert state.messages[4].role == MessageRole.TOOL
        assert state.messages[4].content == "60"
        assert state.messages[5].role == MessageRole.ASSISTANT
        assert state.messages[5].content == "The final result is 60."
        assert len(state.tool_results) == 2
        assert state.tool_results[0].output == 30
        assert state.tool_results[1].output == 60

    asyncio.run(_test())


def test_agent_loop_tool_security_policy_enforcement():
    """Verify tool security policy blocks unconfirmed sensitive tool without crashing the loop."""
    async def _test():
        class SensitiveDeleteTool(Tool):
            @property
            def definition(self) -> ToolDefinition:
                return ToolDefinition(
                    name="delete_file",
                    description="Delete a file",
                    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                    permission=PermissionLevel.CONFIRMATION_REQUIRED,
                )

            async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
                return ToolResult(success=True, output="File deleted")

        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Attempting to delete file...",
            tool_calls=[ToolCall(id="call_del", name="delete_file", arguments={"path": "important.txt"})],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Deletion was blocked by policy, proceeding safely without deletion.",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1, turn_2])
        policy = ToolSecurityPolicy()  # default sets delete_file to CONFIRMATION_REQUIRED
        loop, state_mgr, _, _, tool_reg, _ = setup_test_environment(
            provider=prog_prov, security_policy=policy
        )
        tool_reg.register(SensitiveDeleteTool())

        result = await loop.create_and_run("Delete important.txt")

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 2
        assert "proceeding safely without deletion" in (result.final_response or "")

        state = state_mgr.get_session(result.session_id)
        assert len(state.tool_results) == 1
        assert state.tool_results[0].success is False
        assert "requires user confirmation" in (state.tool_results[0].error or "")
        tool_msg = state.messages[2]
        assert tool_msg.role == MessageRole.TOOL
        assert "Error: Tool 'delete_file' requires user confirmation" in tool_msg.content

    asyncio.run(_test())


def test_agent_loop_tool_security_workspace_boundary(tmp_path: Path):
    """Verify tool security policy blocks workspace path traversal or protected file access."""
    async def _test():
        workspace_dir = tmp_path / "sandbox"
        workspace_dir.mkdir()

        class FileInspectTool(Tool):
            @property
            def definition(self) -> ToolDefinition:
                return ToolDefinition(
                    name="read_file",
                    description="Read file contents",
                    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                    permission=PermissionLevel.ALLOWED,
                )

            async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
                return ToolResult(success=True, output="contents")

        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Reading sensitive .env file...",
            tool_calls=[ToolCall(id="call_read", name="read_file", arguments={"path": ".env"})],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Access to protected file was denied, continuing safely.",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1, turn_2])
        policy = ToolSecurityPolicy(workspace_root=str(workspace_dir))
        loop, state_mgr, _, _, tool_reg, _ = setup_test_environment(
            provider=prog_prov, security_policy=policy
        )
        tool_reg.register(FileInspectTool())

        result = await loop.create_and_run("Read secret .env")

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 2
        state = state_mgr.get_session(result.session_id)
        assert len(state.tool_results) == 1
        assert state.tool_results[0].success is False
        assert "Access to sensitive protected file is BLOCKED" in (state.tool_results[0].error or "")

    asyncio.run(_test())


def test_agent_loop_mid_execution_cancellation():
    """Verify cooperative cancellation triggered mid-execution from an event hook."""
    async def _test():
        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Calling calculator...",
            tool_calls=[ToolCall(id="c1", name="calculate", arguments={"operation": "add", "a": 1, "b": 1})],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Done",
        )

        prog_prov = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1, turn_2])
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=prog_prov)

        def event_listener(ev: Dict[str, Any]):
            # When tool execution starts, trigger cooperative cancellation
            if ev.get("event") == "tool_start":
                loop.cancel(ev["session_id"])

        result = await loop.create_and_run("Calculate something then cancel", on_event=event_listener)

        assert result.status == AgentStatus.CANCELLED
        assert result.cancelled is True
        state = state_mgr.get_session(result.session_id)
        assert state.status == AgentStatus.CANCELLED
        assert state.status.is_terminal is True

    asyncio.run(_test())


def test_agent_loop_all_models_fail_handled():
    """Verify non-recoverable model failure when all fallback candidates fail transitions to FAILED."""
    async def _test():
        provider = MockProvider(provider_id="failing-mock")
        provider.set_simulated_error(
            LamaruError(
                message="Internal API server completely down",
                category=ErrorCategory.TEMPORARY_SERVER_ERROR,
            )
        )
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=provider)

        result = await loop.create_and_run("Execute task with down provider")

        assert result.status == AgentStatus.FAILED
        assert result.cancelled is False
        assert "Model execution failed" in (result.error or "")
        state = state_mgr.get_session(result.session_id)
        assert state.status == AgentStatus.FAILED
        assert any("Model execution failed" in err for err in state.errors)

    asyncio.run(_test())


def test_agent_loop_session_resumption():
    """Verify resuming a previously paused session in WAITING_USER or WAITING_TOOL continues cleanly to completion."""
    async def _test():
        turn_resume_resp = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Resumed session completed successfully!",
            tool_calls=[],
        )
        prog_prov = ProgrammableMockProvider(
            provider_id="mock-prog",
            responses=[turn_resume_resp],
        )
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=prog_prov)

        # 1. Manually create a session and advance to WAITING_USER
        session = state_mgr.create_session("Interactive resumed task")
        session = state_mgr.transition_state(session.session_id, AgentStatus.PLANNING)
        session = state_mgr.transition_state(session.session_id, AgentStatus.RUNNING)
        session = state_mgr.transition_state(session.session_id, AgentStatus.WAITING_USER)
        session.messages.append(Message(role=MessageRole.USER, content="User provided input"))
        state_mgr.save_session(session)

        # 2. Call loop.run on existing session_id
        result = await loop.run(session.session_id)

        assert result.status == AgentStatus.COMPLETED
        assert result.final_response == "Resumed session completed successfully!"

        state = state_mgr.get_session(session.session_id)
        assert state.status == AgentStatus.COMPLETED
        assert state.messages[-1].content == "Resumed session completed successfully!"

    asyncio.run(_test())


def test_agent_loop_session_resumption_from_disk(tmp_path: Path):
    """Verify durable disk persistence allows resuming across different AgentLoop and AgentStateManager instances."""
    async def _test():
        storage_dir = tmp_path / "sessions_resume"

        # Step 1: Run Turn 1 on Loop 1 with max_iterations=1 to pause
        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Calculating 4 + 5",
            tool_calls=[ToolCall(id="c_add", name="calculate", arguments={"operation": "add", "a": 4, "b": 5})],
        )
        prov1 = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1])
        mgr1 = AgentStateManager(storage_dir=storage_dir)

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        prov_reg.register(prov1)
        model = Model(
            provider="mock-prog",
            model_id="test-model-1",
            display_name="Test Model 1",
            context_window=32000,
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
        )
        model_reg.register(model)
        prov1.add_model(model)
        router1 = ModelRouter(model_registry=model_reg, provider_registry=prov_reg)
        tool_reg1 = ToolRegistry()
        tool_reg1.register(MockCalculatorTool())

        loop1 = AgentLoop(
            state_manager=mgr1,
            context_manager=ContextManager(),
            router=router1,
            provider_registry=prov_reg,
            tool_registry=tool_reg1,
        )

        cfg1 = AgentLoopConfig(max_iterations=1)
        res1 = await loop1.create_and_run("Compute 4 + 5", config=cfg1)
        # Hit max iterations = 1
        assert res1.status == AgentStatus.FAILED
        sess_id = res1.session_id

        # Step 2: Now reset state status to WAITING_TOOL in new manager instance and resume with Loop 2
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="The sum is 9.",
            tool_calls=[],
        )
        prov2 = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_2])
        prov_reg2 = ProviderRegistry()
        prov_reg2.register(prov2)
        prov2.add_model(model)
        router2 = ModelRouter(model_registry=model_reg, provider_registry=prov_reg2)
        tool_reg2 = ToolRegistry()
        tool_reg2.register(MockCalculatorTool())

        mgr2 = AgentStateManager(storage_dir=storage_dir)
        loaded_state = mgr2.get_session(sess_id)
        assert loaded_state is not None
        # Reset status from FAILED to WAITING_TOOL for resumption test
        loaded_state.status = AgentStatus.WAITING_TOOL
        mgr2.save_session(loaded_state)

        loop2 = AgentLoop(
            state_manager=mgr2,
            context_manager=ContextManager(),
            router=router2,
            provider_registry=prov_reg2,
            tool_registry=tool_reg2,
        )

        cfg2 = AgentLoopConfig(max_iterations=5)
        res2 = await loop2.run(sess_id, config=cfg2)

        assert res2.status == AgentStatus.COMPLETED
        assert res2.final_response == "The sum is 9."

        final_state = mgr2.get_session(sess_id)
        assert final_state.status == AgentStatus.COMPLETED
        assert final_state.messages[-1].content == "The sum is 9."

    asyncio.run(_test())


def test_agent_loop_event_hooks_sync_and_async():
    """Verify both sync and async on_event listeners receive lifecycle events, and listener exceptions are swallowed safely."""
    async def _test():
        loop, _, _, _, _, _ = setup_test_environment()

        async_events = []
        async def async_listener(ev: Dict[str, Any]):
            await asyncio.sleep(0.001)
            async_events.append(ev["event"])

        result = await loop.create_and_run("Async event hook test", on_event=async_listener)
        assert result.status == AgentStatus.COMPLETED
        assert "planning" in async_events
        assert "started" in async_events
        assert "iteration_start" in async_events
        assert "model_response" in async_events
        assert "completed" in async_events

        # Listener that raises an exception should not crash the loop
        def faulty_listener(ev: Dict[str, Any]):
            raise RuntimeError("Listener exploded!")

        res_faulty = await loop.create_and_run("Faulty listener test", on_event=faulty_listener)
        assert res_faulty.status == AgentStatus.COMPLETED

    asyncio.run(_test())


def test_agent_loop_unregistered_tool_handling():
    """Verify model requesting an unregistered tool receives tool error result without crashing loop."""
    async def _test():
        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Calling phantom tool...",
            tool_calls=[ToolCall(id="c_phantom", name="nonexistent_tool", arguments={"foo": "bar"})],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Tool not found, responding directly.",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1, turn_2])
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=prog_prov)

        result = await loop.create_and_run("Try unregistered tool")

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 2
        state = state_mgr.get_session(result.session_id)
        assert len(state.tool_results) == 1
        assert state.tool_results[0].success is False
        assert "not registered" in state.tool_results[0].error.lower()
        assert state.messages[2].role == MessageRole.TOOL
        assert "not registered" in state.messages[2].content.lower()

    asyncio.run(_test())


def test_agent_loop_tool_exception_handling():
    """Verify tool raising an unhandled exception is caught and returned as ToolResult error."""
    async def _test():
        class ExplodingTool(Tool):
            @property
            def definition(self) -> ToolDefinition:
                return ToolDefinition(name="explode", description="Exploding tool")

            async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
                raise ZeroDivisionError("division by zero inside tool")

        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Calling explode tool...",
            tool_calls=[ToolCall(id="c_boom", name="explode", arguments={})],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Handled explosion gracefully.",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1, turn_2])
        loop, state_mgr, _, _, tool_reg, _ = setup_test_environment(provider=prog_prov)
        tool_reg.register(ExplodingTool())

        result = await loop.create_and_run("Run exploding tool")

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 2
        state = state_mgr.get_session(result.session_id)
        assert len(state.tool_results) == 1
        assert state.tool_results[0].success is False
        assert "division by zero" in (state.tool_results[0].error or "")
        assert state.messages[2].role == MessageRole.TOOL
        assert "division by zero" in state.messages[2].content

    asyncio.run(_test())


def test_agent_loop_relevant_files_tracking_and_loading(tmp_path: Path):
    """Verify file paths referenced in tool calls are recorded in state.relevant_files and loaded into context."""
    async def _test():
        test_file = tmp_path / "sample.py"
        test_file.write_text("print('hello world')", encoding="utf-8")

        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Reading file sample.py",
            tool_calls=[
                ToolCall(
                    id="c_calc",
                    name="calculate",
                    arguments={"operation": "add", "a": 1, "b": 1, "path": str(test_file)},
                )
            ],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Finished inspecting sample.py",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1, turn_2])
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=prog_prov)

        result = await loop.create_and_run(f"Inspect file {test_file}")

        assert result.status == AgentStatus.COMPLETED
        state = state_mgr.get_session(result.session_id)
        assert str(test_file) in state.relevant_files

        # Verify _load_relevant_files loaded the file contents
        loaded_map = loop._load_relevant_files(state.relevant_files)
        assert str(test_file) in loaded_map
        assert loaded_map[str(test_file)] == "print('hello world')"

    asyncio.run(_test())


def test_agent_loop_validation_and_terminal_errors():
    """Verify validation guards: non-existent session, terminal session run, and missing dependencies."""
    async def _test():
        loop, state_mgr, context_mgr, _, _, _ = setup_test_environment()

        # 1. Non-existent session
        with pytest.raises(KeyError, match="Session 'missing_id' not found"):
            await loop.run("missing_id")

        # 2. Terminal session run (CREATED -> RUNNING -> COMPLETED)
        session = state_mgr.create_session("Terminal check")
        state_mgr.transition_state(session.session_id, AgentStatus.RUNNING)
        state_mgr.transition_state(session.session_id, AgentStatus.COMPLETED)
        with pytest.raises(ValueError, match="Cannot run session .* in terminal state COMPLETED"):
            await loop.run(session.session_id)

        # 3. Missing dependencies in constructor
        with pytest.raises(ValueError, match="AgentLoop requires either an existing FallbackEngine"):
            AgentLoop(state_manager=state_mgr, context_manager=context_mgr)

    asyncio.run(_test())


def test_agent_loop_config_options_and_overrides():
    """Verify custom system_prompt, model_override, provider_override, and temperature pass into AIRequest."""
    async def _test():
        prog_prov = ProgrammableMockProvider(
            provider_id="mock",
            responses=[AIResponse(provider="mock", model="test-model-1", content="Custom config OK")],
        )
        loop, _, _, _, _, _ = setup_test_environment(provider=prog_prov)

        cfg = AgentLoopConfig(
            system_prompt="You are a strict QA auditor.",
            model_override="test-model-1",
            provider_override="mock",
            temperature=0.2,
            max_tokens=100,
        )
        result = await loop.create_and_run("Test config overrides", config=cfg)
        assert result.status == AgentStatus.COMPLETED
        assert len(prog_prov.received_requests) == 1
        req = prog_prov.received_requests[0]
        assert "strict QA auditor" in (req.system_prompt or "")
        assert req.temperature == 0.2
        assert req.max_tokens == 100

    asyncio.run(_test())


def test_agent_loop_blocks_protected_files_from_relevant_files(tmp_path: Path):
    """Verify AgentLoop blocks protected files (.env, *.key) from entering relevant_files and context."""
    async def _test():
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        env_file = workspace_dir / ".env"
        env_file.write_text("API_SECRET=super_secret_token", encoding="utf-8")

        # Turn 1: model attempts to read .env
        turn_1 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Attempting to read .env",
            tool_calls=[
                ToolCall(
                    id="call_read_env",
                    name="calculate",
                    arguments={"operation": "add", "a": 1, "b": 1, "path": str(env_file)},
                )
            ],
        )
        turn_2 = AIResponse(
            provider="mock-prog",
            model="test-model-1",
            content="Task completed without leaking .env",
            tool_calls=[],
        )

        prog_prov = ProgrammableMockProvider(provider_id="mock-prog", responses=[turn_1, turn_2])
        loop, state_mgr, _, _, _, _ = setup_test_environment(provider=prog_prov)

        result = await loop.create_and_run("Do not leak .env")
        assert result.status == AgentStatus.COMPLETED

        state = state_mgr.get_session(result.session_id)
        # 1. Protected file .env MUST NOT be in relevant_files
        assert str(env_file) not in state.relevant_files

        # 2. Even if .env was artificially forced into file_paths, _load_relevant_files MUST block it
        loaded = loop._load_relevant_files([str(env_file)])
        assert str(env_file) not in loaded

    asyncio.run(_test())


def test_agent_loop_workspace_boundary_on_relevant_files(tmp_path: Path):
    """Verify AgentLoop enforces WorkspaceGuard boundary on _load_relevant_files."""
    async def _test():
        from lamaru.tools.security import ToolSecurityPolicy

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("outside confidential content", encoding="utf-8")

        inside_file = workspace_dir / "inside.txt"
        inside_file.write_text("inside content", encoding="utf-8")

        policy = ToolSecurityPolicy(workspace_root=str(workspace_dir))
        tool_reg = ToolRegistry(security_policy=policy)

        loop, state_mgr, context_mgr, fallback_engine, _, _ = setup_test_environment()
        loop.tool_registry = tool_reg

        # Loading outside file should be blocked by WorkspaceGuard
        loaded = loop._load_relevant_files([str(outside_file), str(inside_file)])
        assert str(outside_file) not in loaded
        assert str(inside_file) in loaded
        assert loaded[str(inside_file)] == "inside content"

    asyncio.run(_test())


def test_agent_loop_secret_sanitization_in_relevant_files(tmp_path: Path):
    """Verify that file contents loaded into context have secrets and API keys sanitized."""
    async def _test():
        leak_file = tmp_path / "code_with_key.py"
        leak_file.write_text("AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q", encoding="utf-8")

        loop, _, _, _, _, _ = setup_test_environment()
        loaded = loop._load_relevant_files([str(leak_file)])

        assert str(leak_file) in loaded
        assert "[REDACTED_GOOGLE_API_KEY]" in loaded[str(leak_file)]
        assert "AIzaSy" not in loaded[str(leak_file)]

    asyncio.run(_test())

