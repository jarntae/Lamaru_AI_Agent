"""Cooldown lifecycle tracker: HEALTHY -> COOLDOWN -> PROBE -> HEALTHY."""

from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
from typing import Dict, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CooldownState(str, Enum):
    """Lifecycle state of a target in cooldown tracking."""

    HEALTHY = "HEALTHY"
    COOLDOWN = "COOLDOWN"
    PROBE = "PROBE"


class TargetCooldownRecord(BaseModel):
    """State record for a single cooldown target (key, model, or provider)."""

    target_id: str
    state: CooldownState = CooldownState.HEALTHY
    cooldown_until: Optional[datetime] = None
    probe_in_progress: bool = False
    failure_count: int = 0


class CooldownTracker:
    """Tracks cooldown periods and enforces the HEALTHY -> COOLDOWN -> PROBE -> HEALTHY lifecycle.

    Invariants:
    - A candidate in COOLDOWN must NEVER be selected for normal requests.
    - Once cooldown expires, the candidate enters PROBE state, allowing exactly one probe request.
    - If the probe succeeds, it returns to HEALTHY. If it fails, it returns to COOLDOWN.
    """

    def __init__(self) -> None:
        self._records: Dict[str, TargetCooldownRecord] = {}

    def get_state(self, target_id: str, now: Optional[datetime] = None) -> CooldownState:
        """Inspect the current state of a target, transitioning if cooldown expired."""
        record = self._records.get(target_id)
        if not record or record.state == CooldownState.HEALTHY:
            return CooldownState.HEALTHY

        current_time = now or datetime.now(timezone.utc)

        if record.state == CooldownState.COOLDOWN:
            if record.cooldown_until and current_time >= record.cooldown_until:
                record.state = CooldownState.PROBE
                logger.info(f"Target '{target_id}' cooldown expired, transitioned to PROBE")
                return CooldownState.PROBE
            return CooldownState.COOLDOWN

        return record.state

    def is_available(self, target_id: str, now: Optional[datetime] = None) -> bool:
        """Check if a candidate is available for request routing.

        Returns True for HEALTHY targets, or PROBE targets when no probe is currently in flight.
        Returns False for targets in COOLDOWN.
        """
        state = self.get_state(target_id, now=now)
        if state == CooldownState.HEALTHY:
            return True

        if state == CooldownState.PROBE:
            record = self._records[target_id]
            # Allow only one probe at a time
            return not record.probe_in_progress

        # State is COOLDOWN
        return False

    def mark_cooldown(
        self,
        target_id: str,
        duration_seconds: float,
        now: Optional[datetime] = None,
    ) -> None:
        """Move a target into COOLDOWN state for a specified duration."""
        current_time = now or datetime.now(timezone.utc)
        record = self._records.setdefault(
            target_id, TargetCooldownRecord(target_id=target_id)
        )
        record.state = CooldownState.COOLDOWN
        record.cooldown_until = current_time + timedelta(seconds=duration_seconds)
        record.probe_in_progress = False
        record.failure_count += 1
        logger.warning(
            f"Target '{target_id}' placed in COOLDOWN for {duration_seconds}s until {record.cooldown_until}"
        )

    def acquire_probe(self, target_id: str, now: Optional[datetime] = None) -> bool:
        """Attempt to acquire permission to send a probe request to a target in PROBE state."""
        state = self.get_state(target_id, now=now)
        if state != CooldownState.PROBE:
            return False

        record = self._records[target_id]
        if record.probe_in_progress:
            return False

        record.probe_in_progress = True
        return True

    def mark_healthy(self, target_id: str) -> None:
        """Successfully restore a target to HEALTHY state (probe succeeded or manual reset)."""
        if target_id in self._records:
            record = self._records[target_id]
            record.state = CooldownState.HEALTHY
            record.cooldown_until = None
            record.probe_in_progress = False
            record.failure_count = 0
            logger.info(f"Target '{target_id}' restored to HEALTHY")
