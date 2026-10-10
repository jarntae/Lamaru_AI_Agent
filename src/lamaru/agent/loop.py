"""AgentLoop: Core ReAct execution loop coordinating State, Context, ModelRouter, and Tools."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from lamaru.agent.state_manager import AgentStateManager
from lamaru.context.manager import ContextManager
from lamaru.domain.agent import AgentState, AgentStatus, Plan
from lamaru.domain.messages import AIRequest, AIResponse, Message, MessageRole, ToolCall
from lamaru.domain.models import ModelCapability, TaskRequirement
from lamaru.domain.tools import ToolDefinition, ToolResult
from lamaru.providers.registry import ProviderRegistry
from lamaru.router.fallback import FallbackAttempt, FallbackEngine, FallbackResult
from lamaru.router.router import ModelRouter
from lamaru.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentLoopConfig(BaseModel):
    """Configuration options for the AgentLoop execution."""

    max_iterations: int = Field(
        default=25,
        gt=0,
        description="Safety limit for maximum loop iterations",
    )
    timeout_seconds: Optional[float] = Field(
        default=120.0,
        gt=0,
        description="Overall execution timeout in seconds. None or <= 0 for unlimited.",
    )
    max_fallbacks_per_step: int = Field(
        default=5,
        gt=0,
        description="Maximum fallback attempts per model generation step",
    )
    is_user_confirmed: bool = Field(
        default=False,
        description="Pre-authorization flag for tools requiring user confirmation",
    )
    auto_plan: bool = Field(
        default=True,
        description="Whether to perform an initial planning step (CREATED -> PLANNING -> RUNNING)",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="Optional system prompt overriding or prepending context instructions",
    )
    model_override: Optional[str] = Field(
        default=None,
        description="Optional model identifier override for requests",
    )
    provider_override: Optional[str] = Field(
        default=None,
        description="Optional provider identifier override for requests",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for model generations",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        gt=0,
        description="Max token limit per model generation",
    )
    task_type: Optional[str] = Field(
        default=None,
        description="Task type classification (e.g. 'coding', 'general_chat')",
    )
    required_capabilities: Optional[Set[ModelCapability]] = Field(
        default=None,
        description="Optional required model capabilities for candidate routing",
    )


class AgentLoopResult(BaseModel):
    """Structured result returned upon completion or termination of AgentLoop."""

    session_id: str = Field(..., description="Unique session ID of the executed agent")
    status: AgentStatus = Field(..., description="Final terminal status of the session")
    task: str = Field(..., description="The user task executed")
    final_response: Optional[str] = Field(
        default=None, description="Final assistant response text if completed"
    )
    iterations: int = Field(
        default=0, ge=0, description="Total number of loop iterations executed"
    )
    models_used: List[str] = Field(
        default_factory=list, description="List of models utilized during the session"
    )
    tools_invoked: List[str] = Field(
        default_factory=list, description="List of tool names invoked during the session"
    )
    tool_results_count: int = Field(
        default=0, ge=0, description="Total count of tool results produced"
    )
    error: Optional[str] = Field(
        default=None, description="Error message if execution failed or timed out"
    )
    cancelled: bool = Field(
        default=False, description="Whether execution was stopped due to cancellation"
    )
    timed_out: bool = Field(
        default=False, description="Whether execution was stopped due to timeout"
    )
    state: Optional[AgentState] = Field(
        default=None, description="Final AgentState snapshot"
    )


class AgentLoop:
    """Core runtime engine driving the ReAct Agent execution loop.

    Coordinates:
    - AgentStateManager: strict state lifecycle transitions and durable persistence
    - ContextManager: token budgeting, compression, and context tier assembly
    - ModelRouter & FallbackEngine: dynamic candidate selection and automatic model failure recovery
    - ToolRegistry: secure tool execution, workspace boundary guards, and tool timeouts
    """

    def __init__(
        self,
        state_manager: AgentStateManager,
        context_manager: ContextManager,
        fallback_engine: Optional[FallbackEngine] = None,
        tool_registry: Optional[ToolRegistry] = None,
        router: Optional[ModelRouter] = None,
        provider_registry: Optional[ProviderRegistry] = None,
        default_config: Optional[AgentLoopConfig] = None,
    ) -> None:
        self.state_manager = state_manager
        self.context_manager = context_manager
        self.tool_registry = tool_registry
        self.default_config = default_config or AgentLoopConfig()

        if fallback_engine is not None:
            self.fallback_engine = fallback_engine
            self.router = router or getattr(fallback_engine, "router", None)
        elif router is not None and provider_registry is not None:
            self.router = router
            self.fallback_engine = FallbackEngine(
                router=router,
                provider_registry=provider_registry,
                key_manager=router.key_manager,
                cooldown_tracker=router.cooldown_tracker,
            )
        else:
            raise ValueError(
                "AgentLoop requires either an existing FallbackEngine or both router and provider_registry."
            )

        self._cancellation_events: Dict[str, asyncio.Event] = {}
        self._cancelled_sessions: Set[str] = set()

    def cancel(self, session_id: str) -> bool:
        """Request cooperative cancellation for an active or pending session."""
        self._cancelled_sessions.add(session_id)
        if session_id in self._cancellation_events:
            self._cancellation_events[session_id].set()
        logger.info("Cancellation requested for agent session %s", session_id)
        return True

    def is_cancelled(
        self, session_id: str, cancellation_token: Optional[asyncio.Event] = None
    ) -> bool:
        """Check if cancellation has been requested for this session."""
        if session_id in self._cancelled_sessions:
            return True
        if cancellation_token and cancellation_token.is_set():
            return True
        ev = self._cancellation_events.get(session_id)
        if ev and ev.is_set():
            return True
        return False

    async def _emit_event(
        self,
        callback: Optional[Callable[[Dict[str, Any]], Any]],
        event_data: Dict[str, Any],
    ) -> None:
        """Emit an execution event to an optional listener (sync or async)."""
        if callback is None:
            return
        try:
            res = callback(event_data)
            if hasattr(res, "__await__"):
                await res
        except Exception as exc:
            logger.warning("Error in event listener callback: %s", exc)

    def _load_relevant_files(self, file_paths: List[str]) -> Dict[str, str]:
        """Load contents of relevant workspace files up to a safe buffer limit."""
        files_map: Dict[str, str] = {}
        for fp in file_paths:
            try:
                p = Path(fp)
                if p.exists() and p.is_file():
                    files_map[fp] = p.read_text(encoding="utf-8", errors="replace")[:65536]
            except Exception as exc:
                logger.debug("Failed reading relevant file %s: %s", fp, exc)
        return files_map

    async def create_and_run(
        self,
        task: str,
        session_id: Optional[str] = None,
        config: Optional[AgentLoopConfig] = None,
        cancellation_token: Optional[asyncio.Event] = None,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentLoopResult:
        """Create a new session and immediately execute the agent loop."""
        effective_config = config or self.default_config
        state = self.state_manager.create_session(
            task=task,
            session_id=session_id,
            max_iterations=effective_config.max_iterations,
        )
        return await self.run(
            session_id=state.session_id,
            config=effective_config,
            cancellation_token=cancellation_token,
            on_event=on_event,
        )

    async def run(
        self,
        session_id: str,
        config: Optional[AgentLoopConfig] = None,
        cancellation_token: Optional[asyncio.Event] = None,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentLoopResult:
        """Run or resume an Agent session through the ReAct loop until terminal status."""
        state = self.state_manager.get_session(session_id)
        if not state:
            raise KeyError(f"Session '{session_id}' not found.")

        if state.status.is_terminal:
            raise ValueError(
                f"Cannot run session '{session_id}' in terminal state {state.status.value}"
            )

        effective_config = config or self.default_config

        cancel_ev = asyncio.Event()
        self._cancellation_events[session_id] = cancel_ev

        try:
            if effective_config.timeout_seconds and effective_config.timeout_seconds > 0:
                try:
                    return await asyncio.wait_for(
                        self._execute_loop(
                            session_id, effective_config, cancellation_token, on_event
                        ),
                        timeout=effective_config.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    return await self._handle_timeout(
                        session_id, effective_config, on_event
                    )
            else:
                return await self._execute_loop(
                    session_id, effective_config, cancellation_token, on_event
                )
        finally:
            self._cancellation_events.pop(session_id, None)
            self._cancelled_sessions.discard(session_id)

    async def _execute_loop(
        self,
        session_id: str,
        config: AgentLoopConfig,
        cancellation_token: Optional[asyncio.Event],
        on_event: Optional[Callable[[Dict[str, Any]], Any]],
    ) -> AgentLoopResult:
        state = self.state_manager.get_session(session_id)
        if not state:
            raise KeyError(f"Session '{session_id}' not found.")

        # 1. Immediate cancellation check
        if self.is_cancelled(session_id, cancellation_token):
            return await self._handle_cancellation(session_id, on_event)

        # 2. Ensure initial user message exists in state.messages
        if not state.messages:
            state.messages.append(Message(role=MessageRole.USER, content=state.task))
            self.state_manager.save_session(state)

        # 3. State initialization: CREATED -> PLANNING -> RUNNING (or CREATED -> RUNNING)
        if state.status == AgentStatus.CREATED:
            if config.auto_plan:
                state = self.state_manager.transition_state(
                    session_id, AgentStatus.PLANNING
                )
                if not state.plan.steps:
                    state.plan = Plan(
                        steps=[
                            f"Analyze task: {state.task}",
                            "Execute actions using available tools",
                            "Synthesize findings and provide final answer",
                        ],
                        current_step_index=0,
                    )
                    state.current_step = state.plan.steps[0]
                    self.state_manager.save_session(state)

                await self._emit_event(
                    on_event,
                    {
                        "event": "planning",
                        "session_id": session_id,
                        "plan": state.plan.steps,
                        "current_step": state.current_step,
                    },
                )

                if self.is_cancelled(session_id, cancellation_token):
                    return await self._handle_cancellation(session_id, on_event)

                state = self.state_manager.transition_state(
                    session_id, AgentStatus.RUNNING
                )
            else:
                state = self.state_manager.transition_state(
                    session_id, AgentStatus.RUNNING
                )
        elif state.status in (
            AgentStatus.PLANNING,
            AgentStatus.WAITING_TOOL,
            AgentStatus.WAITING_USER,
        ):
            state = self.state_manager.transition_state(session_id, AgentStatus.RUNNING)

        await self._emit_event(
            on_event,
            {
                "event": "started",
                "session_id": session_id,
                "task": state.task,
                "status": state.status.value,
            },
        )

        # 4. ReAct Execution Loop
        while state.iteration_count < config.max_iterations:
            if self.is_cancelled(session_id, cancellation_token):
                return await self._handle_cancellation(session_id, on_event)

            state.iteration_count += 1
            self.state_manager.save_session(state)

            await self._emit_event(
                on_event,
                {
                    "event": "iteration_start",
                    "session_id": session_id,
                    "iteration": state.iteration_count,
                    "max_iterations": config.max_iterations,
                },
            )

            # Assemble context
            relevant_files_content = self._load_relevant_files(state.relevant_files)
            assembled = self.context_manager.assemble_context(
                task=state.task,
                relevant_files=relevant_files_content,
                project_knowledge=state.context_summary if state.context_summary else None,
                messages=state.messages,
                model_context_limit=32000,
            )

            tools = (
                self.tool_registry.get_definitions() if self.tool_registry else []
            )

            # Build prompt instructions
            sys_prompt = config.system_prompt
            if assembled.system_instruction:
                if sys_prompt:
                    sys_prompt = f"{sys_prompt}\n\n{assembled.system_instruction}"
                else:
                    sys_prompt = assembled.system_instruction

            ai_request = AIRequest(
                messages=assembled.messages,
                model=config.model_override or "default",
                provider=config.provider_override,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                tools=tools,
                system_prompt=sys_prompt,
            )

            task_req = TaskRequirement(
                task_type=config.task_type or ("coding" if tools else "general_chat"),
                requires_tools=bool(tools),
                required_capabilities=config.required_capabilities or set(),
            )

            # Call model with FallbackEngine
            try:
                fallback_res = await self.fallback_engine.execute_with_fallback(
                    request=ai_request,
                    task=task_req,
                    max_fallbacks=config.max_fallbacks_per_step,
                    on_fallback=lambda attempt: self._on_fallback(
                        attempt, on_event, session_id
                    ),
                )
            except Exception as exc:
                logger.exception(
                    "Model fallback execution failed for session %s: %s",
                    session_id,
                    exc,
                )
                return await self._handle_model_failure(session_id, exc, on_event)

            cand = fallback_res.selected_candidate
            state.model_history.append(cand.model_id)

            ai_response = fallback_res.response

            await self._emit_event(
                on_event,
                {
                    "event": "model_response",
                    "session_id": session_id,
                    "model": cand.model_id,
                    "provider": cand.provider,
                    "has_tool_calls": bool(ai_response.tool_calls),
                    "tool_call_count": len(ai_response.tool_calls),
                    "content": ai_response.content,
                },
            )

            # Check for tool invocations
            if ai_response.tool_calls:
                assistant_msg = Message(
                    role=MessageRole.ASSISTANT,
                    content=ai_response.content,
                    tool_calls=ai_response.tool_calls,
                )
                state.messages.append(assistant_msg)

                # Strict transition: RUNNING -> WAITING_TOOL
                state = self.state_manager.transition_state(
                    session_id, AgentStatus.WAITING_TOOL
                )
                self.state_manager.save_session(state)

                for tc in ai_response.tool_calls:
                    if self.is_cancelled(session_id, cancellation_token):
                        return await self._handle_cancellation(session_id, on_event)

                    tool_entry = {
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "iteration": state.iteration_count,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    state.tool_history.append(tool_entry)

                    # Track file paths in state.relevant_files if tool touches a file
                    f_path = tc.arguments.get("path") or tc.arguments.get("file_path")
                    if (
                        f_path
                        and isinstance(f_path, str)
                        and f_path not in state.relevant_files
                    ):
                        state.relevant_files.append(f_path)

                    await self._emit_event(
                        on_event,
                        {
                            "event": "tool_start",
                            "session_id": session_id,
                            "tool_call_id": tc.id,
                            "tool_name": tc.name,
                            "arguments": tc.arguments,
                        },
                    )

                    if self.tool_registry:
                        try:
                            tool_result = await self.tool_registry.execute(
                                name=tc.name,
                                arguments=tc.arguments,
                                is_user_confirmed=config.is_user_confirmed,
                            )
                        except Exception as tool_exc:
                            logger.exception(
                                "Unexpected error executing tool %s: %s",
                                tc.name,
                                tool_exc,
                            )
                            tool_result = ToolResult(
                                success=False,
                                error=f"Unexpected error executing tool '{tc.name}': {str(tool_exc)}",
                                metadata={"tool_name": tc.name},
                            )
                    else:
                        tool_result = ToolResult(
                            success=False,
                            error=f"Tool '{tc.name}' requested but no ToolRegistry configured.",
                            metadata={"tool_name": tc.name},
                        )

                    state.tool_results.append(tool_result)

                    await self._emit_event(
                        on_event,
                        {
                            "event": "tool_end",
                            "session_id": session_id,
                            "tool_call_id": tc.id,
                            "tool_name": tc.name,
                            "success": tool_result.success,
                            "error": tool_result.error,
                        },
                    )

                    # Format tool output for message history
                    if isinstance(tool_result.output, (dict, list)):
                        content_str = json.dumps(tool_result.output)
                    elif tool_result.output is not None:
                        content_str = str(tool_result.output)
                    elif tool_result.error:
                        content_str = f"Error: {tool_result.error}"
                    else:
                        content_str = ""

                    tool_msg = Message(
                        role=MessageRole.TOOL,
                        content=content_str,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                    state.messages.append(tool_msg)

                # Durable state save after tool execution turn
                self.state_manager.save_session(state)

                if self.is_cancelled(session_id, cancellation_token):
                    return await self._handle_cancellation(session_id, on_event)

                # Strict transition: WAITING_TOOL -> RUNNING
                state = self.state_manager.transition_state(
                    session_id, AgentStatus.RUNNING
                )
                self.state_manager.save_session(state)

                # Advance plan step
                if (
                    state.plan.steps
                    and state.plan.current_step_index < len(state.plan.steps) - 1
                ):
                    state.plan.current_step_index = min(
                        state.plan.current_step_index + 1, len(state.plan.steps) - 1
                    )
                    state.current_step = (
                        state.plan.current_step or state.current_step
                    )
                    self.state_manager.save_session(state)

            else:
                # Final response (No tool calls) -> Model completed task!
                assistant_msg = Message(
                    role=MessageRole.ASSISTANT,
                    content=ai_response.content,
                )
                state.messages.append(assistant_msg)

                if state.plan.steps:
                    state.plan.current_step_index = len(state.plan.steps) - 1
                    state.current_step = "Completed"

                # Strict transition: RUNNING -> COMPLETED
                state = self.state_manager.transition_state(
                    session_id, AgentStatus.COMPLETED
                )
                self.state_manager.save_session(state)

                await self._emit_event(
                    on_event,
                    {
                        "event": "completed",
                        "session_id": session_id,
                        "status": AgentStatus.COMPLETED.value,
                        "iterations": state.iteration_count,
                        "final_response": ai_response.content,
                    },
                )

                return self._build_result(state, final_response=ai_response.content)

        # Exceeded max iterations
        error_msg = f"Maximum iterations ({config.max_iterations}) reached without completion."
        state.errors.append(error_msg)
        state = self.state_manager.transition_state(session_id, AgentStatus.FAILED)
        self.state_manager.save_session(state)

        await self._emit_event(
            on_event,
            {
                "event": "failed",
                "session_id": session_id,
                "error": error_msg,
            },
        )

        return self._build_result(state, error=error_msg)

    async def _on_fallback(
        self,
        attempt: FallbackAttempt,
        on_event: Optional[Callable[[Dict[str, Any]], Any]],
        session_id: str,
    ) -> None:
        """Handle fallback notification from FallbackEngine."""
        logger.warning(
            "Session '%s' fallback: candidate '%s' failed: %s",
            session_id,
            attempt.candidate.target_key,
            attempt.error_message,
        )
        await self._emit_event(
            on_event,
            {
                "event": "model_fallback",
                "session_id": session_id,
                "target": attempt.candidate.target_key,
                "error": attempt.error_message,
                "category": attempt.error_category.value
                if attempt.error_category
                else None,
            },
        )

    async def _handle_cancellation(
        self,
        session_id: str,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentLoopResult:
        """Handle session cancellation transition."""
        state = self.state_manager.get_session(session_id)
        error_msg = "Execution cancelled by user or client request."
        if state and not state.status.is_terminal:
            state.errors.append(error_msg)
            state = self.state_manager.transition_state(
                session_id, AgentStatus.CANCELLED
            )
            self.state_manager.save_session(state)

        await self._emit_event(
            on_event,
            {
                "event": "cancelled",
                "session_id": session_id,
                "status": AgentStatus.CANCELLED.value,
            },
        )

        return self._build_result(state, error=error_msg, cancelled=True)

    async def _handle_timeout(
        self,
        session_id: str,
        config: AgentLoopConfig,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentLoopResult:
        """Handle overall execution timeout transition."""
        state = self.state_manager.get_session(session_id)
        error_msg = (
            f"Agent execution timed out after {config.timeout_seconds} seconds."
        )
        if state and not state.status.is_terminal:
            state.errors.append(error_msg)
            state = self.state_manager.transition_state(
                session_id, AgentStatus.FAILED
            )
            self.state_manager.save_session(state)

        await self._emit_event(
            on_event,
            {
                "event": "timeout",
                "session_id": session_id,
                "error": error_msg,
            },
        )

        return self._build_result(state, error=error_msg, timed_out=True)

    async def _handle_model_failure(
        self,
        session_id: str,
        exc: Exception,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentLoopResult:
        """Handle non-recoverable model failure transition."""
        state = self.state_manager.get_session(session_id)
        error_msg = f"Model execution failed: {str(exc)}"
        if state and not state.status.is_terminal:
            state.errors.append(error_msg)
            state = self.state_manager.transition_state(
                session_id, AgentStatus.FAILED
            )
            self.state_manager.save_session(state)

        await self._emit_event(
            on_event,
            {
                "event": "failed",
                "session_id": session_id,
                "error": error_msg,
            },
        )

        return self._build_result(state, error=error_msg)

    def _build_result(
        self,
        state: Optional[AgentState],
        final_response: Optional[str] = None,
        error: Optional[str] = None,
        cancelled: bool = False,
        timed_out: bool = False,
    ) -> AgentLoopResult:
        """Construct structured AgentLoopResult from current AgentState."""
        if state is None:
            return AgentLoopResult(
                session_id="unknown",
                status=AgentStatus.FAILED,
                task="",
                error=error,
                cancelled=cancelled,
                timed_out=timed_out,
            )

        tools_invoked = [
            entry.get("name", "")
            for entry in state.tool_history
            if entry.get("name")
        ]

        return AgentLoopResult(
            session_id=state.session_id,
            status=state.status,
            task=state.task,
            final_response=final_response,
            iterations=state.iteration_count,
            models_used=list(dict.fromkeys(state.model_history)),
            tools_invoked=tools_invoked,
            tool_results_count=len(state.tool_results),
            error=error or (state.errors[-1] if state.errors else None),
            cancelled=cancelled or state.status == AgentStatus.CANCELLED,
            timed_out=timed_out,
            state=state,
        )
