"""AgentState manager enforcing valid transitions, disk persistence, and session recovery."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
import uuid

from lamaru.domain.agent import AgentState, AgentStatus, Plan
from lamaru.tools.security import SecretSanitizer

logger = logging.getLogger(__name__)

# Strict transition graph
ALLOWED_TRANSITIONS: Dict[AgentStatus, Set[AgentStatus]] = {
    AgentStatus.CREATED: {
        AgentStatus.PLANNING,
        AgentStatus.RUNNING,
        AgentStatus.CANCELLED,
    },
    AgentStatus.PLANNING: {
        AgentStatus.RUNNING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.RUNNING: {
        AgentStatus.WAITING_TOOL,
        AgentStatus.WAITING_USER,
        AgentStatus.PLANNING,
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.WAITING_TOOL: {
        AgentStatus.RUNNING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.WAITING_USER: {
        AgentStatus.RUNNING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.COMPLETED: set(),
    AgentStatus.FAILED: set(),
    AgentStatus.CANCELLED: set(),
}


class AgentStateManager:
    """Manages AgentState lifecycle transitions, durable disk persistence, and resume recovery."""

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = storage_dir
        if self.storage_dir:
            self.storage_dir.mkdir(parents=True, exist_ok=True)

        self._in_memory_sessions: Dict[str, AgentState] = {}

    def _get_file_path(self, session_id: str) -> Optional[Path]:
        if self.storage_dir:
            # Sanitize session_id to prevent directory traversal
            clean_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
            return self.storage_dir / f"{clean_id}.json"
        return None

    def create_session(
        self,
        task: str,
        session_id: Optional[str] = None,
        max_iterations: int = 25,
    ) -> AgentState:
        """Create a new agent session in CREATED status."""
        sid = session_id or f"session-{uuid.uuid4().hex[:10]}"
        state = AgentState(
            session_id=sid,
            task=task,
            status=AgentStatus.CREATED,
            max_iterations=max_iterations,
        )
        self.save_session(state)
        return state

    def save_session(self, state: AgentState) -> None:
        """Persist agent state in memory and to disk if storage_dir is configured."""
        state.updated_at = datetime.now(timezone.utc)
        self._in_memory_sessions[state.session_id] = state

        file_path = self._get_file_path(state.session_id)
        if file_path:
            raw_json = state.to_json()
            safe_json = SecretSanitizer.sanitize_text(raw_json) or raw_json
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(safe_json)

    def get_session(self, session_id: str) -> Optional[AgentState]:
        """Retrieve session from memory or load from disk persistence."""
        if session_id in self._in_memory_sessions:
            return self._in_memory_sessions[session_id]

        file_path = self._get_file_path(session_id)
        if file_path and file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    state = AgentState.from_json(content)
                    self._in_memory_sessions[session_id] = state
                    return state
            except Exception as exc:
                logger.error(f"Failed to load session '{session_id}' from disk: {exc}")
                return None

        return None

    def transition_state(self, session_id: str, new_status: AgentStatus) -> AgentState:
        """Transition session to a new status following valid state transition rules."""
        state = self.get_session(session_id)
        if not state:
            raise KeyError(f"Agent session '{session_id}' not found.")

        current_status = state.status

        # Validate transition
        allowed = ALLOWED_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition from {current_status.value} to {new_status.value}. "
                f"Allowed transitions: {[s.value for s in allowed]}"
            )

        state.status = new_status
        self.save_session(state)
        logger.info(
            f"Session '{session_id}' transitioned {current_status.value} -> {new_status.value}"
        )
        return state

    def resume_session(self, session_id: str) -> AgentState:
        """Resume a session after process restart, validating it is resumable."""
        state = self.get_session(session_id)
        if not state:
            raise KeyError(f"Cannot resume: session '{session_id}' not found.")

        if state.status.is_terminal:
            raise ValueError(
                f"Cannot resume session '{session_id}' in terminal state {state.status.value}"
            )

        # If it was waiting on tool/user, keep that state, otherwise ensure it is in RUNNING/PLANNING
        logger.info(f"Resumed session '{session_id}' in status {state.status.value}")
        return state

    def list_sessions(self) -> List[AgentState]:
        """List all active and stored sessions."""
        sessions: Dict[str, AgentState] = dict(self._in_memory_sessions)

        if self.storage_dir and self.storage_dir.exists():
            for p in self.storage_dir.glob("*.json"):
                sid = p.stem
                if sid not in sessions:
                    loaded = self.get_session(sid)
                    if loaded:
                        sessions[sid] = loaded

        return list(sessions.values())
