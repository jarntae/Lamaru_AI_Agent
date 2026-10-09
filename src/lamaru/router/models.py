"""Routing models, candidate targets, scoring weights, and results."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from lamaru.domain.models import Model, TaskRequirement


class CandidateTarget(BaseModel):
    """A specific candidate target (provider, model, and optional key) for executing a request."""

    provider: str
    model_id: str
    key_id: Optional[str] = None
    model: Model
    score: float = 0.0
    score_breakdown: Dict[str, float] = Field(default_factory=dict)

    @property
    def target_key(self) -> str:
        """Unique key identifying this candidate combination."""
        if self.key_id:
            return f"{self.provider}:{self.model_id}:{self.key_id}"
        return f"{self.provider}:{self.model_id}"


class RouterScoringWeights(BaseModel):
    """Configurable scoring weights for candidate evaluation."""

    weight_capability_match: float = 30.0
    weight_task_match: float = 20.0
    weight_priority: float = 15.0
    weight_health: float = 15.0
    weight_quota: float = 10.0
    weight_latency: float = 10.0
    failure_penalty_weight: float = 25.0


class RoutingResult(BaseModel):
    """Outcome of a router evaluation containing the ranked candidates and chosen target."""

    selected_candidate: Optional[CandidateTarget] = None
    ranked_candidates: List[CandidateTarget] = Field(default_factory=list)
    task_requirement: TaskRequirement
    evaluated_count: int = 0

    @property
    def has_candidate(self) -> bool:
        return self.selected_candidate is not None
