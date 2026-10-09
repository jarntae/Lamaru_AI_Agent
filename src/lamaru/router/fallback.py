"""Fallback engine executing requests across candidates with state preservation."""

from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import AIRequest, AIResponse
from lamaru.domain.models import TaskRequirement
from lamaru.errors.classifier import ErrorClassifier
from lamaru.keys.manager import APIKeyManager
from lamaru.providers.registry import ProviderRegistry
from lamaru.resilience.cooldown import CooldownTracker
from lamaru.router.models import CandidateTarget
from lamaru.router.router import ModelRouter

logger = logging.getLogger(__name__)


class FallbackAttempt(BaseModel):
    """Record of an attempt made against a specific candidate target."""

    candidate: CandidateTarget
    success: bool
    error_message: Optional[str] = None
    error_category: Optional[ErrorCategory] = None
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.candidate.target_key,
            "success": self.success,
            "error_message": self.error_message,
            "error_category": self.error_category.value if self.error_category else None,
            "attempted_at": self.attempted_at.isoformat(),
        }


class FallbackResult(BaseModel):
    """Result of an execution with fallback."""

    response: AIResponse
    selected_candidate: CandidateTarget
    attempts: List[FallbackAttempt] = Field(default_factory=list)
    success: bool = True

    @property
    def fallback_count(self) -> int:
        """Number of failed attempts before the successful one."""
        return max(0, len(self.attempts) - 1)


class FallbackEngine:
    """Orchestrates request execution across dynamically ranked candidates when failures occur.

    Adheres to the recommended hierarchy:
    1. Same model + another healthy key
    2. Same provider + compatible model
    3. Another provider
    4. Local model

    Maintains AgentState and Request integrity across all switches.
    """

    def __init__(
        self,
        router: ModelRouter,
        provider_registry: ProviderRegistry,
        key_manager: Optional[APIKeyManager] = None,
        cooldown_tracker: Optional[CooldownTracker] = None,
        error_classifier: Optional[ErrorClassifier] = None,
    ) -> None:
        self.router = router
        self.provider_registry = provider_registry
        self.key_manager = key_manager
        self.cooldown_tracker = cooldown_tracker
        self.error_classifier = error_classifier or ErrorClassifier()

    async def execute_with_fallback(
        self,
        request: AIRequest,
        task: Optional[TaskRequirement] = None,
        max_fallbacks: int = 5,
        on_fallback: Optional[Callable[[FallbackAttempt], Any]] = None,
        now: Optional[datetime] = None,
    ) -> FallbackResult:
        """Execute request across fallback candidates until success or all candidates exhausted."""
        current_time = now or datetime.now(timezone.utc)

        # Derive task requirements if not explicitly provided
        task_req = task or TaskRequirement(
            task_type="coding" if request.tools else "general_chat",
            requires_tools=bool(request.tools),
        )

        attempted_keys: Set[str] = set()
        attempts_history: List[FallbackAttempt] = []

        for attempt_idx in range(1, max_fallbacks + 1):
            # 1. Ask router for best available candidate excluding already attempted ones
            route_res = self.router.route(
                task=task_req,
                min_context_window=request.max_tokens,
                exclude_candidates=attempted_keys,
                now=current_time,
            )

            if not route_res.has_candidate:
                logger.error("All fallback candidates exhausted for request.")
                raise LamaruError(
                    message="All available model/provider fallback candidates exhausted.",
                    category=ErrorCategory.QUOTA_EXCEEDED,
                    details={"attempts": [a.to_dict() for a in attempts_history]},
                )

            cand = route_res.selected_candidate
            attempted_keys.add(cand.target_key)

            # 2. Retrieve provider adapter
            provider = self.provider_registry.get(cand.provider)
            if not provider or not provider.is_enabled:
                attempt = FallbackAttempt(
                    candidate=cand,
                    success=False,
                    error_message=f"Provider '{cand.provider}' is disabled or not found",
                    error_category=ErrorCategory.UNKNOWN,
                    attempted_at=current_time,
                )
                attempts_history.append(attempt)
                continue

            # 3. Clone request targeting this specific candidate
            target_req = request.model_copy(deep=True)
            target_req.model = cand.model_id
            target_req.provider = cand.provider
            target_req.key_id = cand.key_id

            logger.info(
                f"Fallback attempt {attempt_idx}/{max_fallbacks} -> {cand.target_key}"
            )

            # 4. Attempt generation
            try:
                response = await provider.generate(target_req)

                # Record success
                if cand.key_id and self.key_manager:
                    self.key_manager.record_key_success(cand.key_id)

                if self.cooldown_tracker:
                    self.cooldown_tracker.mark_healthy(f"{cand.provider}:{cand.model_id}")

                success_attempt = FallbackAttempt(
                    candidate=cand,
                    success=True,
                    attempted_at=current_time,
                )
                attempts_history.append(success_attempt)

                return FallbackResult(
                    response=response,
                    selected_candidate=cand,
                    attempts=attempts_history,
                    success=True,
                )

            except Exception as exc:
                classification = self.error_classifier.classify_exception(exc)

                # Record attempt failure
                failed_attempt = FallbackAttempt(
                    candidate=cand,
                    success=False,
                    error_message=str(exc),
                    error_category=classification.category,
                    attempted_at=current_time,
                )
                attempts_history.append(failed_attempt)

                if on_fallback:
                    cb = on_fallback(failed_attempt)
                    if hasattr(cb, "__await__"):
                        await cb

                # Handle Cooldown / Disabling based on policy
                cooldown_sec = (
                    classification.cooldown_seconds
                    if classification.cooldown_seconds > 0
                    else 60.0
                )

                if cand.key_id and self.key_manager:
                    if classification.action.value == "DISABLE_KEY":
                        key_obj = self.key_manager.get_key(cand.key_id)
                        if key_obj:
                            key_obj.enabled = False
                    else:
                        self.key_manager.record_key_failure(
                            cand.key_id,
                            error_msg=str(exc),
                            cooldown_seconds=cooldown_sec,
                            now=current_time,
                        )

                # If non-retryable request-level error (e.g. prompt safety block, invalid syntax)
                if classification.category in (
                    ErrorCategory.SAFETY_BLOCK,
                    ErrorCategory.INVALID_REQUEST,
                ):
                    logger.warning(
                        f"Non-fallbackable request error ({classification.category.value}): {exc}"
                    )
                    raise exc

                # Otherwise (429, 500, Auth on this key, Model not found, Quota exceeded) -> Fallback to next candidate!
                logger.warning(
                    f"Candidate {cand.target_key} failed with {classification.category.value}. Initiating fallback."
                )

        # Max fallbacks exceeded
        raise LamaruError(
            message=f"All fallback candidates exhausted (max attempts {max_fallbacks} reached).",
            category=ErrorCategory.QUOTA_EXCEEDED,
            details={"attempts": [a.to_dict() for a in attempts_history]},
        )
