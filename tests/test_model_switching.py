"""Tests for Phase 17: Model Switching During Agent execution."""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from lamaru.agent.loop import AgentLoop, AgentLoopConfig
from lamaru.agent.state_manager import AgentStateManager
from lamaru.context.manager import ContextManager
from lamaru.domain.agent import AgentStatus
from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import (
    AIRequest,
    AIResponse,
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
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
    """MockProvider that responds with queued responses or errors per call."""

    def __init__(
        self,
        provider_id: str,
        responses: Optional[List[Any]] = None,
        models: Optional[List[Model]] = None,
    ) -> None:
        super().__init__(provider_id=provider_id, models=models)
        self.queue: List[Any] = responses or []
        self.requests: List[AIRequest] = []

    async def generate(self, request: AIRequest) -> AIResponse:
        self._call_count += 1
        self.requests.append(request)

        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        return await super().generate(request)


class MockCalculatorTool(Tool):
    """Simple calculation tool for testing state preservation."""

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
        return ToolResult(success=False, error=f"Unknown op {op}")


class MockFileReadTool(Tool):
    """Simple file reading tool for testing context preservation across model switches."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read file contents from workspace",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            timeout_seconds=5.0,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        p = Path(input_args.get("path", ""))
        if p.exists() and p.is_file():
            content = p.read_text(encoding="utf-8")
            return ToolResult(success=True, output=content)
        return ToolResult(success=False, error=f"File not found: {p}")


def test_phase17_model_switching_mid_agent_scenario():
    """Verify Phase 17 scenario:
    Turn 1: Model A makes tool call.
    Tool executes and records in AgentState.
    Turn 2: Model A fails with 429 Too Many Requests.
    Router selects Model B.
    Model B receives full context (task, plan, tool history, previous results, current step).
    Model B completes without restarting from scratch.
    state.model_history records both ['model-a', 'model-b'].
    'model_switched' event is emitted with from_model, to_model, reason, session_id.
    """
    async def _test():
        # Turn 1: Model A responds with a tool call
        turn_1_resp = AIResponse(
            provider="provider-a",
            model="model-a",
            content="I will add 10 and 20 first.",
            tool_calls=[
                ToolCall(
                    id="tc_add_1",
                    name="calculate",
                    arguments={"operation": "add", "a": 10, "b": 20},
                )
            ],
            finish_reason=FinishReason.TOOL_CALLS,
        )

        # Turn 2: Model A fails with 429
        rate_limit_err = LamaruError(
            message="Resource exhausted: 429 Too Many Requests",
            category=ErrorCategory.RATE_LIMIT,
            provider="provider-a",
        )

        # Provider A: Turn 1 success -> Turn 2 429 error
        prov_a = ProgrammableMockProvider(
            provider_id="provider-a",
            responses=[turn_1_resp, rate_limit_err],
        )

        # Provider B: Turn 2 fallback success
        turn_2_resp = AIResponse(
            provider="provider-b",
            model="model-b",
            content="The sum of 10 and 20 is 30.",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
        )
        prov_b = ProgrammableMockProvider(
            provider_id="provider-b",
            responses=[turn_2_resp],
        )

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()
        cooldown = CooldownTracker()
        error_classifier = ErrorClassifier()

        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        # Model A (higher priority)
        model_a = Model(
            provider="provider-a",
            model_id="model-a",
            display_name="Model A",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING, ModelCapability.FAST},
            context_window=64000,
            priority=10,
        )
        model_reg.register(model_a)
        prov_a.add_model(model_a)
        key_mgr.register_key(
            APIKeyMetadata(key_id="k-a", provider="provider-a", secret_ref=SecretReference.from_raw("key_a"))
        )

        # Model B (fallback candidate)
        model_b = Model(
            provider="provider-b",
            model_id="model-b",
            display_name="Model B",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING, ModelCapability.FAST},
            context_window=32000,
            priority=5,
        )
        model_reg.register(model_b)
        prov_b.add_model(model_b)
        key_mgr.register_key(
            APIKeyMetadata(key_id="k-b", provider="provider-b", secret_ref=SecretReference.from_raw("key_b"))
        )

        router = ModelRouter(
            model_registry=model_reg,
            provider_registry=prov_reg,
            key_manager=key_mgr,
            cooldown_tracker=cooldown,
        )
        fallback_engine = FallbackEngine(
            router=router,
            provider_registry=prov_reg,
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

        events: List[Dict[str, Any]] = []
        def on_event(ev: Dict[str, Any]) -> None:
            events.append(ev)

        result = await loop.create_and_run(
            task="Calculate 10 + 20 and report answer",
            on_event=on_event,
        )

        # 1. Execution completed successfully
        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 2
        assert result.final_response == "The sum of 10 and 20 is 30."
        assert result.tools_invoked == ["calculate"]
        assert result.tool_results_count == 1

        # 2. Both models recorded in model progression across turns
        assert result.models_used == ["model-a", "model-b"]
        state = state_mgr.get_session(result.session_id)
        assert state is not None
        assert state.model_history == ["model-a", "model-b"]

        # 3. Model switched event emitted with required fields
        model_switch_events = [e for e in events if e.get("event") == "model_switched"]
        assert len(model_switch_events) >= 1
        switch_ev = model_switch_events[0]
        assert switch_ev["from_model"] == "model-a"
        assert switch_ev["to_model"] == "model-b"
        assert "429" in switch_ev["reason"] or "Too Many Requests" in switch_ev["reason"]
        assert switch_ev["session_id"] == result.session_id

        # 4. AgentState strictly preserved without restarting from scratch
        assert state.task == "Calculate 10 + 20 and report answer"
        assert len(state.tool_history) == 1
        assert state.tool_history[0]["name"] == "calculate"
        assert len(state.tool_results) == 1
        assert state.tool_results[0].success is True
        assert state.tool_results[0].output == 30
        assert len(state.plan.steps) > 0
        assert state.current_step == "Completed"

        # 5. Model B received full context in request
        assert len(prov_b.requests) == 1
        req_b = prov_b.requests[0]
        msg_roles = [m.role for m in req_b.messages]
        # Must contain USER, ASSISTANT (tool call from Model A), and TOOL (result)
        assert MessageRole.USER in msg_roles
        assert MessageRole.ASSISTANT in msg_roles
        assert MessageRole.TOOL in msg_roles
        tool_msg = next(m for m in req_b.messages if m.role == MessageRole.TOOL)
        assert tool_msg.content == "30"
        assert "Execution Plan" in (req_b.system_prompt or "")

    asyncio.run(_test())


def test_dynamic_context_budgeting_per_candidate_window():
    """Verify that candidate models with smaller context windows receive appropriately budgeted requests without overflowing."""
    async def _test():
        # Large model A fails, fallback to Model B with small context window (2000 tokens)
        prov_a = MockProvider(provider_id="prov-a")
        prov_a.set_simulated_error(
            LamaruError(
                message="Model A rate limited 429",
                category=ErrorCategory.RATE_LIMIT,
            )
        )

        turn_resp = AIResponse(
            provider="prov-b",
            model="model-small",
            content="Task completed with budgeted context.",
            tool_calls=[],
        )
        prov_b = ProgrammableMockProvider(
            provider_id="prov-b",
            responses=[turn_resp],
        )

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()
        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        # Model A: 64000 window
        model_a = Model(
            provider="prov-a",
            model_id="model-a",
            display_name="Large Model A",
            capabilities={ModelCapability.CODING},
            context_window=64000,
            priority=10,
        )
        # Model Small: 2000 window
        model_small = Model(
            provider="prov-b",
            model_id="model-small",
            display_name="Small Model",
            capabilities={ModelCapability.CODING},
            context_window=2000,
            priority=5,
        )
        model_reg.register(model_a)
        model_reg.register(model_small)
        prov_a.add_model(model_a)
        prov_b.add_model(model_small)

        key_mgr.register_key(APIKeyMetadata(key_id="ka", provider="prov-a", secret_ref=SecretReference.from_raw("ka")))
        key_mgr.register_key(APIKeyMetadata(key_id="kb", provider="prov-b", secret_ref=SecretReference.from_raw("kb")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr)
        fallback_engine = FallbackEngine(router=router, provider_registry=prov_reg, key_manager=key_mgr)

        state_mgr = AgentStateManager()
        context_mgr = ContextManager()

        loop = AgentLoop(
            state_manager=state_mgr,
            context_manager=context_mgr,
            fallback_engine=fallback_engine,
        )

        events: List[Dict[str, Any]] = []
        result = await loop.create_and_run(
            task="Analyze data under token constraints",
            on_event=lambda ev: events.append(ev),
        )

        assert result.status == AgentStatus.COMPLETED
        assert result.final_response == "Task completed with budgeted context."
        assert "model-small" in result.models_used

        # Check that Model Small's received request was dynamically budgeted to fit within 2000
        req_small = prov_b.requests[0]
        assert req_small.max_tokens is not None
        assert req_small.max_tokens <= 2000
        # Total tokens in req_small messages must fit within input budget
        total_msg_tokens = sum(context_mgr.estimate_message_tokens(m) for m in req_small.messages)
        assert total_msg_tokens <= 2000

        # model_switched event emitted
        switches = [e for e in events if e.get("event") == "model_switched"]
        assert len(switches) >= 1
        assert switches[0]["from_model"] == "model-a"
        assert switches[0]["to_model"] == "model-small"

    asyncio.run(_test())


def test_model_switching_within_turn1_fallback():
    """Verify model switch event is emitted even when fallback happens on the very first turn."""
    async def _test():
        prov_a = MockProvider(provider_id="prov-a")
        prov_a.set_simulated_error(
            LamaruError(message="Service Unavailable 503", category=ErrorCategory.TEMPORARY_SERVER_ERROR)
        )

        prov_b = MockProvider(provider_id="prov-b", default_response="Recovered immediately on Model B")

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()
        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        model_a = Model(
            provider="prov-a",
            model_id="model-primary",
            display_name="Primary",
            capabilities={ModelCapability.CODING},
            context_window=32000,
            priority=10,
        )
        model_b = Model(
            provider="prov-b",
            model_id="model-secondary",
            display_name="Secondary",
            capabilities={ModelCapability.CODING},
            context_window=32000,
            priority=5,
        )
        model_reg.register(model_a)
        model_reg.register(model_b)
        prov_a.add_model(model_a)
        prov_b.add_model(model_b)
        key_mgr.register_key(APIKeyMetadata(key_id="k1", provider="prov-a", secret_ref=SecretReference.from_raw("k1")))
        key_mgr.register_key(APIKeyMetadata(key_id="k2", provider="prov-b", secret_ref=SecretReference.from_raw("k2")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr)
        fallback = FallbackEngine(router=router, provider_registry=prov_reg, key_manager=key_mgr)

        loop = AgentLoop(
            state_manager=AgentStateManager(),
            context_manager=ContextManager(),
            fallback_engine=fallback,
        )

        events: List[Dict[str, Any]] = []
        result = await loop.create_and_run(
            task="Test turn 1 failover",
            on_event=lambda e: events.append(e),
        )

        assert result.status == AgentStatus.COMPLETED
        assert result.final_response == "Recovered immediately on Model B"

        switch_events = [e for e in events if e.get("event") == "model_switched"]
        assert len(switch_events) == 1
        assert switch_events[0]["from_model"] == "model-primary"
        assert switch_events[0]["to_model"] == "model-secondary"
        assert "503" in switch_events[0]["reason"] or "Unavailable" in switch_events[0]["reason"]

    asyncio.run(_test())


def test_multi_model_switching_chain_three_models():
    """Verify multi-model chain across iterations: Model A (Turn 1) -> Model B (Turn 2) -> Model C (Turn 3)."""
    async def _test():
        # Model A: Turn 1 tool call -> Turn 2 429 error
        turn_1_resp = AIResponse(
            provider="provider-a",
            model="model-a",
            content="Compute 5 + 5",
            tool_calls=[ToolCall(id="c1", name="calculate", arguments={"operation": "add", "a": 5, "b": 5})],
            finish_reason=FinishReason.TOOL_CALLS,
        )
        err_a = LamaruError(
            message="Model A 429 Rate Limit",
            category=ErrorCategory.RATE_LIMIT,
            provider="provider-a",
        )
        prov_a = ProgrammableMockProvider(
            provider_id="provider-a",
            responses=[turn_1_resp, err_a],
        )

        # Model B: Turn 2 tool call -> Turn 3 503 error
        turn_2_resp = AIResponse(
            provider="provider-b",
            model="model-b",
            content="Multiply 10 * 3",
            tool_calls=[ToolCall(id="c2", name="calculate", arguments={"operation": "multiply", "a": 10, "b": 3})],
            finish_reason=FinishReason.TOOL_CALLS,
        )
        err_b = LamaruError(
            message="Model B 503 Service Unavailable",
            category=ErrorCategory.TEMPORARY_SERVER_ERROR,
            provider="provider-b",
        )
        prov_b = ProgrammableMockProvider(
            provider_id="provider-b",
            responses=[turn_2_resp, err_b],
        )

        # Model C: Turn 3 final completion
        turn_3_resp = AIResponse(
            provider="provider-c",
            model="model-c",
            content="The final result is 30.",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
        )
        prov_c = ProgrammableMockProvider(
            provider_id="provider-c",
            responses=[turn_3_resp],
        )

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()
        cooldown = CooldownTracker()
        error_classifier = ErrorClassifier()

        prov_reg.register(prov_a)
        prov_reg.register(prov_b)
        prov_reg.register(prov_c)

        model_a = Model(
            provider="provider-a",
            model_id="model-a",
            display_name="Model A",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
            priority=10,
        )
        model_b = Model(
            provider="provider-b",
            model_id="model-b",
            display_name="Model B",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
            priority=5,
        )
        model_c = Model(
            provider="provider-c",
            model_id="model-c",
            display_name="Model C",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
            priority=1,
        )
        model_reg.register(model_a)
        model_reg.register(model_b)
        model_reg.register(model_c)
        prov_a.add_model(model_a)
        prov_b.add_model(model_b)
        prov_c.add_model(model_c)

        key_mgr.register_key(APIKeyMetadata(key_id="k-a", provider="provider-a", secret_ref=SecretReference.from_raw("ka")))
        key_mgr.register_key(APIKeyMetadata(key_id="k-b", provider="provider-b", secret_ref=SecretReference.from_raw("kb")))
        key_mgr.register_key(APIKeyMetadata(key_id="k-c", provider="provider-c", secret_ref=SecretReference.from_raw("kc")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr, cooldown_tracker=cooldown)
        fallback = FallbackEngine(
            router=router,
            provider_registry=prov_reg,
            key_manager=key_mgr,
            cooldown_tracker=cooldown,
            error_classifier=error_classifier,
        )

        state_mgr = AgentStateManager()
        tool_reg = ToolRegistry()
        tool_reg.register(MockCalculatorTool())

        loop = AgentLoop(
            state_manager=state_mgr,
            context_manager=ContextManager(),
            fallback_engine=fallback,
            tool_registry=tool_reg,
        )

        events: List[Dict[str, Any]] = []
        result = await loop.create_and_run(
            task="Calculate (5 + 5) * 3",
            on_event=lambda e: events.append(e),
        )

        assert result.status == AgentStatus.COMPLETED
        assert result.iterations == 3
        assert result.final_response == "The final result is 30."
        assert result.models_used == ["model-a", "model-b", "model-c"]
        assert result.tools_invoked == ["calculate", "calculate"]
        assert result.tool_results_count == 2

        state = state_mgr.get_session(result.session_id)
        assert state is not None
        assert state.model_history == ["model-a", "model-b", "model-c"]
        assert len(state.tool_results) == 2
        assert state.tool_results[0].output == 10
        assert state.tool_results[1].output == 30

        switches = [e for e in events if e.get("event") == "model_switched"]
        assert len(switches) >= 2
        assert switches[0]["from_model"] == "model-a"
        assert switches[0]["to_model"] == "model-b"
        assert switches[1]["from_model"] == "model-b"
        assert switches[1]["to_model"] == "model-c"

    asyncio.run(_test())


def test_session_resumption_with_model_switch(tmp_path: Path):
    """Verify resuming a saved session with a different model preserves prior tool calls, plan, and emits model switch."""
    async def _test():
        storage_dir = tmp_path / "sessions_model_switch"

        # Model A previously executed Turn 1 and paused in WAITING_TOOL
        state_mgr1 = AgentStateManager(storage_dir=storage_dir)
        session = state_mgr1.create_session("Resume task with new model")
        session = state_mgr1.transition_state(session.session_id, AgentStatus.PLANNING)
        session = state_mgr1.transition_state(session.session_id, AgentStatus.RUNNING)
        session = state_mgr1.transition_state(session.session_id, AgentStatus.WAITING_TOOL)
        session.model_history.append("model-a")
        session.tool_history.append({
            "tool_call_id": "c1",
            "name": "calculate",
            "arguments": {"operation": "add", "a": 5, "b": 10},
        })
        session.tool_results.append(ToolResult(success=True, output=15))
        session.messages.append(Message(role=MessageRole.USER, content=session.task))
        session.messages.append(
            Message(
                role=MessageRole.ASSISTANT,
                content="Adding 5 + 10",
                tool_calls=[ToolCall(id="c1", name="calculate", arguments={"operation": "add", "a": 5, "b": 10})],
            )
        )
        session.messages.append(
            Message(role=MessageRole.TOOL, content="15", tool_call_id="c1", name="calculate")
        )
        state_mgr1.save_session(session)
        sess_id = session.session_id

        # Setup runtime where Model A is failing, Model B is available
        prov_a = MockProvider(provider_id="provider-a")
        prov_a.set_simulated_error(
            LamaruError(message="Model A 429 quota exhausted", category=ErrorCategory.RATE_LIMIT, provider="provider-a")
        )

        turn_b_resp = AIResponse(
            provider="provider-b",
            model="model-b",
            content="Resumed and finished with Model B. The sum is 15.",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
        )
        prov_b = ProgrammableMockProvider(
            provider_id="provider-b",
            responses=[turn_b_resp],
        )

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()

        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        model_a = Model(
            provider="provider-a",
            model_id="model-a",
            display_name="Model A",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
            priority=10,
        )
        model_b = Model(
            provider="provider-b",
            model_id="model-b",
            display_name="Model B",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
            priority=5,
        )
        model_reg.register(model_a)
        model_reg.register(model_b)
        prov_a.add_model(model_a)
        prov_b.add_model(model_b)

        key_mgr.register_key(APIKeyMetadata(key_id="k-a", provider="provider-a", secret_ref=SecretReference.from_raw("ka")))
        key_mgr.register_key(APIKeyMetadata(key_id="k-b", provider="provider-b", secret_ref=SecretReference.from_raw("kb")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr)
        fallback = FallbackEngine(router=router, provider_registry=prov_reg, key_manager=key_mgr)

        state_mgr2 = AgentStateManager(storage_dir=storage_dir)
        tool_reg = ToolRegistry()
        tool_reg.register(MockCalculatorTool())

        loop = AgentLoop(
            state_manager=state_mgr2,
            context_manager=ContextManager(),
            fallback_engine=fallback,
            tool_registry=tool_reg,
        )

        events: List[Dict[str, Any]] = []
        result = await loop.run(sess_id, on_event=lambda e: events.append(e))

        assert result.status == AgentStatus.COMPLETED
        assert result.final_response == "Resumed and finished with Model B. The sum is 15."
        assert "model-b" in result.models_used

        state = state_mgr2.get_session(sess_id)
        assert state is not None
        assert state.model_history == ["model-a", "model-b"]
        assert len(state.tool_results) == 1
        assert state.tool_results[0].output == 15

        switch_events = [e for e in events if e.get("event") == "model_switched"]
        assert len(switch_events) >= 1
        assert switch_events[0]["from_model"] == "model-a"
        assert switch_events[0]["to_model"] == "model-b"

    asyncio.run(_test())


def test_fallback_exhaustion_all_models_fail_rate_limit():
    """Verify when all fallback candidates fail with RATE_LIMIT, execution gracefully terminates with FAILED."""
    async def _test():
        prov_a = MockProvider(provider_id="prov-a")
        prov_a.set_simulated_error(LamaruError("Prov A 429", category=ErrorCategory.RATE_LIMIT, provider="prov-a"))
        prov_b = MockProvider(provider_id="prov-b")
        prov_b.set_simulated_error(LamaruError("Prov B 429", category=ErrorCategory.RATE_LIMIT, provider="prov-b"))

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()

        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        model_a = Model(
            provider="prov-a",
            model_id="model-a",
            display_name="Model A",
            capabilities={ModelCapability.CODING},
            context_window=32000,
            priority=10,
        )
        model_b = Model(
            provider="prov-b",
            model_id="model-b",
            display_name="Model B",
            capabilities={ModelCapability.CODING},
            context_window=32000,
            priority=5,
        )
        model_reg.register(model_a)
        model_reg.register(model_b)
        prov_a.add_model(model_a)
        prov_b.add_model(model_b)

        key_mgr.register_key(APIKeyMetadata(key_id="k-a", provider="prov-a", secret_ref=SecretReference.from_raw("ka")))
        key_mgr.register_key(APIKeyMetadata(key_id="k-b", provider="prov-b", secret_ref=SecretReference.from_raw("kb")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr)
        fallback = FallbackEngine(router=router, provider_registry=prov_reg, key_manager=key_mgr)

        state_mgr = AgentStateManager()
        loop = AgentLoop(
            state_manager=state_mgr,
            context_manager=ContextManager(),
            fallback_engine=fallback,
        )

        events: List[Dict[str, Any]] = []
        result = await loop.create_and_run("Exhaustion test", on_event=lambda e: events.append(e))

        assert result.status == AgentStatus.FAILED
        assert result.cancelled is False
        assert "Model execution failed" in (result.error or "")

        state = state_mgr.get_session(result.session_id)
        assert state is not None
        assert state.status == AgentStatus.FAILED
        assert any("Model execution failed" in e for e in state.errors)

        failed_events = [e for e in events if e.get("event") == "failed"]
        assert len(failed_events) >= 1

    asyncio.run(_test())


def test_dynamic_context_compression_with_history_overflow():
    """Verify that switching to a model with very small context window triggers context compression without crashing."""
    async def _test():
        # Prepopulate state with many conversation messages
        state_mgr = AgentStateManager()
        session = state_mgr.create_session("Task with large history")
        for i in range(12):
            session.messages.append(
                Message(
                    role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                    content=f"Message {i}: " + ("This is detailed historical message context. " * 15),
                )
            )
        state_mgr.save_session(session)

        # Provider A (fails with 429)
        prov_a = MockProvider(provider_id="prov-a")
        prov_a.set_simulated_error(LamaruError("Model A 429", category=ErrorCategory.RATE_LIMIT, provider="prov-a"))

        # Provider B (tight 1500 context window, responds successfully)
        turn_resp = AIResponse(
            provider="prov-b",
            model="model-tiny",
            content="Summary under tight budget completed.",
            tool_calls=[],
        )
        prov_b = ProgrammableMockProvider(
            provider_id="prov-b",
            responses=[turn_resp],
        )

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()

        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        model_a = Model(
            provider="prov-a",
            model_id="model-a",
            display_name="Large Model",
            capabilities={ModelCapability.CODING},
            context_window=128000,
            priority=10,
        )
        model_tiny = Model(
            provider="prov-b",
            model_id="model-tiny",
            display_name="Tiny Model",
            capabilities={ModelCapability.CODING},
            context_window=1500,
            priority=5,
        )
        model_reg.register(model_a)
        model_reg.register(model_tiny)
        prov_a.add_model(model_a)
        prov_b.add_model(model_tiny)

        key_mgr.register_key(APIKeyMetadata(key_id="ka", provider="prov-a", secret_ref=SecretReference.from_raw("ka")))
        key_mgr.register_key(APIKeyMetadata(key_id="kb", provider="prov-b", secret_ref=SecretReference.from_raw("kb")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr)
        fallback = FallbackEngine(router=router, provider_registry=prov_reg, key_manager=key_mgr)

        context_mgr = ContextManager()
        loop = AgentLoop(
            state_manager=state_mgr,
            context_manager=context_mgr,
            fallback_engine=fallback,
        )

        result = await loop.run(session.session_id)

        assert result.status == AgentStatus.COMPLETED
        assert result.final_response == "Summary under tight budget completed."
        assert "model-tiny" in result.models_used

        # Check that Model Tiny's received request tokens fit within 1500 limit
        req_tiny = prov_b.requests[0]
        total_tokens = sum(context_mgr.estimate_message_tokens(m) for m in req_tiny.messages)
        assert total_tokens <= 1500

    asyncio.run(_test())


def test_model_switching_with_file_context_preservation(tmp_path: Path):
    """Verify relevant workspace files discovered by Model A are preserved and passed to Model B after model switch."""
    async def _test():
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        sample_file = workspace_dir / "config.txt"
        sample_file.write_text("DATABASE_HOST=db.internal\nPORT=5432", encoding="utf-8")

        # Turn 1: Model A calls read_file
        turn_1 = AIResponse(
            provider="prov-a",
            model="model-a",
            content="I will read config.txt first.",
            tool_calls=[ToolCall(id="c_read", name="read_file", arguments={"path": str(sample_file)})],
            finish_reason=FinishReason.TOOL_CALLS,
        )
        # Turn 2: Model A fails with 429
        err_a = LamaruError("429 rate limit", category=ErrorCategory.RATE_LIMIT, provider="prov-a")
        prov_a = ProgrammableMockProvider(provider_id="prov-a", responses=[turn_1, err_a])

        # Turn 2: Model B finishes using file data
        turn_2 = AIResponse(
            provider="prov-b",
            model="model-b",
            content="Configuration loaded: DATABASE_HOST=db.internal on port 5432.",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
        )
        prov_b = ProgrammableMockProvider(provider_id="prov-b", responses=[turn_2])

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()

        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        model_a = Model(
            provider="prov-a",
            model_id="model-a",
            display_name="Model A",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
            priority=10,
        )
        model_b = Model(
            provider="prov-b",
            model_id="model-b",
            display_name="Model B",
            capabilities={ModelCapability.CODING, ModelCapability.TOOL_CALLING},
            context_window=32000,
            priority=5,
        )
        model_reg.register(model_a)
        model_reg.register(model_b)
        prov_a.add_model(model_a)
        prov_b.add_model(model_b)

        key_mgr.register_key(APIKeyMetadata(key_id="ka", provider="prov-a", secret_ref=SecretReference.from_raw("ka")))
        key_mgr.register_key(APIKeyMetadata(key_id="kb", provider="prov-b", secret_ref=SecretReference.from_raw("kb")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr)
        fallback = FallbackEngine(router=router, provider_registry=prov_reg, key_manager=key_mgr)

        state_mgr = AgentStateManager()
        tool_reg = ToolRegistry()
        tool_reg.register(MockFileReadTool())

        loop = AgentLoop(
            state_manager=state_mgr,
            context_manager=ContextManager(),
            fallback_engine=fallback,
            tool_registry=tool_reg,
        )

        events: List[Dict[str, Any]] = []
        result = await loop.create_and_run(
            f"Read configuration at {sample_file}",
            on_event=lambda e: events.append(e),
        )

        assert result.status == AgentStatus.COMPLETED
        assert "DATABASE_HOST=db.internal" in (result.final_response or "")
        assert result.models_used == ["model-a", "model-b"]

        state = state_mgr.get_session(result.session_id)
        assert state is not None
        assert str(sample_file) in state.relevant_files
        assert len(state.tool_results) == 1
        assert "DATABASE_HOST" in state.tool_results[0].output

        switches = [e for e in events if e.get("event") == "model_switched"]
        assert len(switches) >= 1
        assert switches[0]["from_model"] == "model-a"
        assert switches[0]["to_model"] == "model-b"

    asyncio.run(_test())


def test_model_switched_event_async_listener_and_resilience():
    """Verify async event listeners receive model_switched with all payload attributes, and listener failures do not break execution."""
    async def _test():
        prov_a = MockProvider(provider_id="prov-a")
        prov_a.set_simulated_error(LamaruError("Failover trigger", category=ErrorCategory.RATE_LIMIT, provider="prov-a"))
        prov_b = MockProvider(provider_id="prov-b", default_response="Handled smoothly")

        model_reg = ModelRegistry()
        prov_reg = ProviderRegistry()
        key_mgr = APIKeyManager()

        prov_reg.register(prov_a)
        prov_reg.register(prov_b)

        model_a = Model(
            provider="prov-a",
            model_id="model-primary",
            display_name="Primary",
            capabilities={ModelCapability.CODING},
            context_window=32000,
            priority=10,
        )
        model_b = Model(
            provider="prov-b",
            model_id="model-secondary",
            display_name="Secondary",
            capabilities={ModelCapability.CODING},
            context_window=32000,
            priority=5,
        )
        model_reg.register(model_a)
        model_reg.register(model_b)
        prov_a.add_model(model_a)
        prov_b.add_model(model_b)

        key_mgr.register_key(APIKeyMetadata(key_id="k1", provider="prov-a", secret_ref=SecretReference.from_raw("k1")))
        key_mgr.register_key(APIKeyMetadata(key_id="k2", provider="prov-b", secret_ref=SecretReference.from_raw("k2")))

        router = ModelRouter(model_registry=model_reg, provider_registry=prov_reg, key_manager=key_mgr)
        fallback = FallbackEngine(router=router, provider_registry=prov_reg, key_manager=key_mgr)

        loop = AgentLoop(
            state_manager=AgentStateManager(),
            context_manager=ContextManager(),
            fallback_engine=fallback,
        )

        captured_switches: List[Dict[str, Any]] = []

        async def async_listener(ev: Dict[str, Any]) -> None:
            await asyncio.sleep(0.001)
            if ev.get("event") == "model_switched":
                captured_switches.append(ev)

        result = await loop.create_and_run("Async event listener test", on_event=async_listener)
        assert result.status == AgentStatus.COMPLETED
        assert len(captured_switches) == 1
        ev = captured_switches[0]
        assert ev["event"] == "model_switched"
        assert ev["from_model"] == "model-primary"
        assert ev["to_model"] == "model-secondary"
        assert ev["session_id"] == result.session_id
        assert isinstance(ev["reason"], str)

        # Faulty listener that throws should not break the agent
        def faulty_listener(ev: Dict[str, Any]) -> None:
            raise RuntimeError("Buggy callback listener!")

        res_faulty = await loop.create_and_run("Faulty listener test", on_event=faulty_listener)
        assert res_faulty.status == AgentStatus.COMPLETED

    asyncio.run(_test())

