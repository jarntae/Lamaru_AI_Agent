import pytest
from lamaru.domain.agent import AgentState, AgentStatus, Plan
from lamaru.domain.messages import Message, MessageRole, ToolCall
from lamaru.domain.tools import ToolResult


def test_agent_state_lifecycle_transitions():
    state = AgentState(
        session_id="session-001",
        task="Create unit test for gateway",
        plan=Plan(steps=["Step 1", "Step 2"], current_step_index=0),
    )
    assert state.status == AgentStatus.CREATED
    assert state.plan.current_step == "Step 1"

    state.transition_to(AgentStatus.PLANNING)
    assert state.status == AgentStatus.PLANNING

    state.transition_to(AgentStatus.RUNNING)
    assert state.status == AgentStatus.RUNNING

    state.transition_to(AgentStatus.COMPLETED)
    assert state.status == AgentStatus.COMPLETED
    assert state.status.is_terminal is True

    # Transitioning from terminal state must raise ValueError
    with pytest.raises(ValueError):
        state.transition_to(AgentStatus.RUNNING)


def test_agent_state_json_serialization_roundtrip():
    state = AgentState(
        session_id="session-12345",
        task="Implement mock provider",
        plan=Plan(
            steps=["Inspect interface", "Write mock class", "Run tests"],
            current_step_index=1,
        ),
        current_step="Write mock class",
        messages=[
            Message(role=MessageRole.USER, content="Implement mock provider"),
            Message(
                role=MessageRole.ASSISTANT,
                content="I will create the file.",
                tool_calls=[
                    ToolCall(
                        id="call_99",
                        name="write_file",
                        arguments={"path": "mock.py", "content": "..."},
                    )
                ],
            ),
        ],
        context_summary="Working in root repository",
        relevant_files=["src/lamaru/domain/provider.py"],
        tool_history=[{"action": "write_file", "path": "mock.py"}],
        tool_results=[
            ToolResult(success=True, output="File created", metadata={"duration": 0.05})
        ],
        errors=[],
        model_history=["gemini-2.5-flash"],
        iteration_count=2,
        max_iterations=10,
        status=AgentStatus.RUNNING,
    )

    json_str = state.to_json()
    assert isinstance(json_str, str)
    assert "session-12345" in json_str
    assert "Implement mock provider" in json_str

    deserialized = AgentState.from_json(json_str)
    assert deserialized.session_id == state.session_id
    assert deserialized.task == state.task
    assert deserialized.plan.steps == state.plan.steps
    assert deserialized.plan.current_step_index == 1
    assert deserialized.plan.current_step == "Write mock class"
    assert len(deserialized.messages) == 2
    assert deserialized.messages[1].tool_calls[0].id == "call_99"
    assert deserialized.relevant_files == ["src/lamaru/domain/provider.py"]
    assert deserialized.tool_results[0].success is True
    assert deserialized.tool_results[0].output == "File created"
    assert deserialized.model_history == ["gemini-2.5-flash"]
    assert deserialized.iteration_count == 2
    assert deserialized.status == AgentStatus.RUNNING
