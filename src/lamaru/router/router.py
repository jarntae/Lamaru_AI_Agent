"""ModelRouter: Dynamic scoring, capability matching, and candidate selection."""

from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Set, Tuple

from lamaru.domain.models import ModelCapability, TaskRequirement
from lamaru.keys.manager import APIKeyManager
from lamaru.models.registry import ModelRegistry
from lamaru.providers.registry import ProviderRegistry
from lamaru.resilience.circuit_breaker import CircuitBreaker, CircuitState
from lamaru.resilience.cooldown import CooldownTracker
from lamaru.router.models import CandidateTarget, RouterScoringWeights, RoutingResult

logger = logging.getLogger(__name__)


class ModelRouter:
    """Intelligent router selecting the optimal candidate target based on dynamic scoring.

    Follows the architectural invariant:
    - Never uses hard-coded fallback chains (if A -> B -> C).
    - Dynamically evaluates candidates based on capabilities, task match, priority, health, and availability.
    """

    def __init__(
        self,
        model_registry: ModelRegistry,
        provider_registry: ProviderRegistry,
        key_manager: Optional[APIKeyManager] = None,
        cooldown_tracker: Optional[CooldownTracker] = None,
        circuit_breakers: Optional[Dict[str, CircuitBreaker]] = None,
        scoring_weights: Optional[RouterScoringWeights] = None,
    ) -> None:
        self.model_registry = model_registry
        self.provider_registry = provider_registry
        self.key_manager = key_manager
        self.cooldown_tracker = cooldown_tracker
        self.circuit_breakers = circuit_breakers or {}
        self.weights = scoring_weights or RouterScoringWeights()

    def _is_target_available(
        self,
        provider: str,
        model_id: str,
        key_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """Check availability against cooldowns and circuit breakers."""
        # 1. Provider availability in registry
        p = self.provider_registry.get(provider)
        if not p or not p.is_enabled:
            return False

        # 2. Circuit breaker check for (provider:model)
        cb_key = f"{provider}:{model_id}"
        if cb_key in self.circuit_breakers:
            if not self.circuit_breakers[cb_key].can_execute(now=now):
                return False

        # 3. Cooldown check for (provider:model)
        if self.cooldown_tracker:
            if not self.cooldown_tracker.is_available(cb_key, now=now):
                return False

        # 4. Key availability check
        if key_id and self.key_manager:
            k = self.key_manager.get_key(key_id)
            if not k or not k.is_available(now=now):
                return False
            if self.cooldown_tracker and not self.cooldown_tracker.is_available(key_id, now=now):
                return False

        return True

    def calculate_score(
        self,
        candidate: CandidateTarget,
        task: TaskRequirement,
        health_latency_ms: float = 0.0,
        provider_health_status: str = "healthy",
        failure_count: int = 0,
        success_count: int = 0,
    ) -> Tuple[float, Dict[str, float]]:
        """Calculate dynamic score for a candidate target based on multi-criteria evaluation."""
        model = candidate.model

        # 1. Capability match (0.0 - 1.0)
        required_caps = set(task.required_capabilities)
        if task.requires_tools:
            required_caps.add(ModelCapability.TOOL_CALLING)
        if task.requires_reasoning:
            required_caps.add(ModelCapability.REASONING)
        if task.requires_long_context:
            required_caps.add(ModelCapability.LONG_CONTEXT)

        if required_caps:
            matched_caps = required_caps.intersection(model.capabilities)
            cap_score = len(matched_caps) / len(required_caps)
        else:
            cap_score = 1.0

        # 2. Task match affinity (0.0 - 1.0)
        task_match = 0.5
        task_type_lower = task.task_type.lower()
        if any(w in task_type_lower for w in ("code", "coding", "debug", "refactor")):
            if ModelCapability.CODING in model.capabilities:
                task_match = 1.0
        elif any(w in task_type_lower for w in ("plan", "reason", "architect")):
            if ModelCapability.REASONING in model.capabilities:
                task_match = 1.0
        elif any(w in task_type_lower for w in ("fast", "chat", "summary")):
            if ModelCapability.FAST in model.capabilities:
                task_match = 1.0

        # 3. Model priority normalized (0.0 - 1.0)
        priority_score = min(1.0, max(0.0, model.priority / 10.0))

        # 4. Health status (0.0 - 1.0)
        health_score = 1.0 if provider_health_status == "healthy" else (
            0.5 if provider_health_status == "degraded" else 0.0
        )

        # 5. Quota / Reliability score (0.0 - 1.0)
        total_runs = success_count + failure_count
        if total_runs > 0:
            quota_score = success_count / total_runs
        else:
            quota_score = 1.0

        # 6. Latency score (higher score for lower latency)
        latency_score = 1.0 / (1.0 + (health_latency_ms / 1000.0))

        # 7. Failure penalty (0.0 - 1.0)
        failure_penalty = min(1.0, failure_count * 0.25)

        # Total weighted score
        w = self.weights
        total_score = (
            (w.weight_capability_match * cap_score)
            + (w.weight_task_match * task_match)
            + (w.weight_priority * priority_score)
            + (w.weight_health * health_score)
            + (w.weight_quota * quota_score)
            + (w.weight_latency * latency_score)
            - (w.failure_penalty_weight * failure_penalty)
        )

        breakdown = {
            "capability_match": round(cap_score, 3),
            "task_match": round(task_match, 3),
            "priority": round(priority_score, 3),
            "health": round(health_score, 3),
            "quota": round(quota_score, 3),
            "latency": round(latency_score, 3),
            "failure_penalty": round(failure_penalty, 3),
            "total_score": round(total_score, 3),
        }

        return round(total_score, 3), breakdown

    def route(
        self,
        task: TaskRequirement,
        min_context_window: Optional[int] = None,
        exclude_candidates: Optional[Set[str]] = None,
        now: Optional[datetime] = None,
    ) -> RoutingResult:
        """Execute the candidate selection and dynamic scoring pipeline."""
        current_time = now or datetime.now(timezone.utc)
        excluded = exclude_candidates or set()

        # Step 1: Filter compatible models from ModelRegistry
        compatible_models = self.model_registry.filter_candidates(
            task, min_context_window=min_context_window, enabled_only=True
        )

        candidates: List[CandidateTarget] = []

        # Step 2: Expand models into CandidateTargets (with keys if managed)
        for model in compatible_models:
            provider_id = model.provider

            if self.key_manager:
                available_keys = self.key_manager.get_available_keys(
                    provider_id, now=current_time
                )
                if available_keys:
                    for k in available_keys:
                        candidates.append(
                            CandidateTarget(
                                provider=provider_id,
                                model_id=model.model_id,
                                key_id=k.key_id,
                                model=model,
                            )
                        )
                else:
                    # If provider does not require/have keys in manager (e.g. local)
                    candidates.append(
                        CandidateTarget(
                            provider=provider_id,
                            model_id=model.model_id,
                            key_id=None,
                            model=model,
                        )
                    )
            else:
                candidates.append(
                    CandidateTarget(
                        provider=provider_id,
                        model_id=model.model_id,
                        key_id=None,
                        model=model,
                    )
                )

        # Step 3: Remove unavailable and excluded candidates
        usable_candidates: List[CandidateTarget] = []
        for cand in candidates:
            if cand.target_key in excluded:
                continue

            if not self._is_target_available(
                cand.provider, cand.model_id, cand.key_id, now=current_time
            ):
                continue

            usable_candidates.append(cand)

        # Step 4: Score all remaining candidates
        for cand in usable_candidates:
            fail_count = 0
            succ_count = 0
            if cand.key_id and self.key_manager:
                km = self.key_manager.get_key(cand.key_id)
                if km:
                    fail_count = km.failure_count
                    succ_count = km.success_count

            score, breakdown = self.calculate_score(
                candidate=cand,
                task=task,
                failure_count=fail_count,
                success_count=succ_count,
            )
            cand.score = score
            cand.score_breakdown = breakdown

        # Step 5: Rank candidates by score descending
        usable_candidates.sort(key=lambda c: c.score, reverse=True)

        selected = usable_candidates[0] if usable_candidates else None

        return RoutingResult(
            selected_candidate=selected,
            ranked_candidates=usable_candidates,
            task_requirement=task,
            evaluated_count=len(usable_candidates),
        )
