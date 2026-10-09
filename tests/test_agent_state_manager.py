from pathlib import Path
import pytest

from lamaru.agent.state_manager import AgentStateManager
from lamaru.domain.agent import AgentStatus, Plan
from lamaru.domain.messages import Message, MessageRole


def test_agent_state_valid_transitions():
    mgr = AgentStateManager()
    state = mgr.create_session("Build feature A", session_id="session-001")
    assert state.status == AgentStatus.CREATED

    # CREATED -> PLANNING
    s1 = mgr.transition_state("session-001", AgentStatus.PLANNING)
    assert s1.status == AgentStatus.PLANNING

    # PLANNING -> RUNNING
    s2 = mgr.transition_state("session-001", AgentStatus.RUNNING)
    assert s2.status == AgentStatus.RUNNING

    # RUNNING -> WAITING_TOOL
    s3 = mgr.transition_state("session-001", AgentStatus.WAITING_TOOL)
    assert s3.status == AgentStatus.WAITING_TOOL

    # WAITING_TOOL -> RUNNING
    s4 = mgr.transition_state("session-001", AgentStatus.RUNNING)
    assert s4.status == AgentStatus.RUNNING

    # RUNNING -> WAITING_USER
    s5 = mgr.transition_state("session-001", AgentStatus.WAITING_USER)
    assert s5.status == AgentStatus.WAITING_USER

    # WAITING_USER -> RUNNING
    s6 = mgr.transition_state("session-001", AgentStatus.RUNNING)
    assert s6.status == AgentStatus.RUNNING

    # RUNNING -> COMPLETED
    s7 = mgr.transition_state("session-001", AgentStatus.COMPLETED)
    assert s7.status == AgentStatus.COMPLETED


def test_agent_state_invalid_transitions():
    mgr = AgentStateManager()
    mgr.create_session("Task", session_id="session-inv")

    # Invalid: CREATED -> WAITING_TOOL
    with pytest.raises(ValueError) as exc:
        mgr.transition_state("session-inv", AgentStatus.WAITING_TOOL)
    assert "Invalid transition" in str(exc.value)

    # Move to COMPLETED
    mgr.transition_state("session-inv", AgentStatus.RUNNING)
    mgr.transition_state("session-inv", AgentStatus.COMPLETED)

    # Invalid: Transitioning from terminal COMPLETED
    with pytest.raises(ValueError):
        mgr.transition_state("session-inv", AgentStatus.RUNNING)


def test_restart_and_resume_recovery(tmp_path: Path):
    """Specification requirement: ทดสอบ restart แล้ว resume ได้."""
    storage_dir = tmp_path / "sessions"

    # Process 1: Manager instance before restart
    mgr1 = AgentStateManager(storage_dir=storage_dir)
    state = mgr1.create_session("Implement compiler optimizer", session_id="sess-res-1")
    state.plan = Plan(steps=["Lexer", "Parser", "Codegen"], current_step_index=1)
    state.current_step = "Parser"
    state.messages.append(Message(role=MessageRole.USER, content="Start optimization"))
    state.messages.append(Message(role=MessageRole.ASSISTANT, content="Parsing AST..."))
    state.iteration_count = 3
    mgr1.save_session(state)

    mgr1.transition_state("sess-res-1", AgentStatus.RUNNING)

    # Simulate Process / Gateway Restart:
    # Destroy mgr1 and all in-memory references
    del mgr1

    # Process 2: Brand new Manager instance after restart
    mgr2 = AgentStateManager(storage_dir=storage_dir)

    # Verify session is discovered and loaded from disk
    recovered = mgr2.resume_session("sess-res-1")
    assert recovered is not None
    assert recovered.session_id == "sess-res-1"
    assert recovered.task == "Implement compiler optimizer"
    assert recovered.status == AgentStatus.RUNNING
    assert recovered.plan.steps == ["Lexer", "Parser", "Codegen"]
    assert recovered.plan.current_step == "Parser"
    assert len(recovered.messages) == 2
    assert recovered.iteration_count == 3

    # Verify session can continue lifecycle to completion after resume
    mgr2.transition_state("sess-res-1", AgentStatus.COMPLETED)
    assert mgr2.get_session("sess-res-1").status == AgentStatus.COMPLETED
