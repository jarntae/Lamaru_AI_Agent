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
from lamaru.domain.tools import Tool, ToolDefinition, ToolResult
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


def setup_test_environment(provider: Optional[MockProvider] = None):
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

    tool_reg = ToolRegistry()
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
