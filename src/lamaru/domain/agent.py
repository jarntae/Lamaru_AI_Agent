"""Agent state machine and representation."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from lamaru.domain.messages import Message
from lamaru.domain.tools import ToolResult


class AgentStatus(str, Enum):
    """Lifecycle status of an Agent session."""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_USER = "WAITING_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        """Indicates whether this state is terminal."""
        return self in {
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
        }


class Plan(BaseModel):
    """Execution plan created or updated by the Agent."""

    steps: List[str] = Field(default_factory=list, description="Ordered steps of the plan")
    current_step_index: int = Field(default=0, ge=0, description="Index of the currently active step")

    @property
    def current_step(self) -> Optional[str]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None


class AgentState(BaseModel):
    """Persistent and transferable state of an Agent session."""

    session_id: str = Field(..., description="Unique identifier for the session")
    task: str = Field(..., description="User-requested task or goal")
    plan: Plan = Field(default_factory=Plan, description="Current execution plan")
    current_step: str = Field(default="", description="Description of the active step")
    messages: List[Message] = Field(
        default_factory=list, description="Conversation and reasoning history"
    )
    context_summary: str = Field(
        default="", description="Compressed or prioritized context summary"
    )
    relevant_files: List[str] = Field(
        default_factory=list, description="List of workspace files relevant to the task"
    )
    tool_history: List[Dict[str, Any]] = Field(
        default_factory=list, description="Audit log of tool invocations"
    )
    tool_results: List[ToolResult] = Field(
        default_factory=list, description="Results from executed tools"
    )
    errors: List[str] = Field(
        default_factory=list, description="Non-fatal and fatal errors encountered"
    )
    model_history: List[str] = Field(
        default_factory=list, description="History of models utilized in this session"
    )
    iteration_count: int = Field(
        default=0, ge=0, description="Number of execution iterations completed"
    )
    max_iterations: int = Field(
        default=25, gt=0, description="Safety limit for maximum iterations"
    )
    status: AgentStatus = Field(
        default=AgentStatus.CREATED, description="Current lifecycle status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Session creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Session last update timestamp",
    )

    def transition_to(self, new_status: AgentStatus) -> None:
        """Transition the state to a new status with validation."""
        if self.status.is_terminal:
            raise ValueError(f"Cannot transition from terminal state {self.status} to {new_status}")
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)

    def to_json(self) -> str:
        """Serialize AgentState to JSON string."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "AgentState":
        """Deserialize AgentState from JSON string."""
        return cls.model_validate_json(json_str)
