from datetime import datetime, timezone
import pytest

from lamaru.domain.models import Model, ModelCapability, TaskRequirement
from lamaru.keys.manager import APIKeyManager
from lamaru.keys.models import APIKeyMetadata, SecretReference
from lamaru.models.registry import ModelRegistry
from lamaru.providers.mock import MockProvider
from lamaru.providers.registry import ProviderRegistry
from lamaru.resilience.circuit_breaker import CircuitBreaker
from lamaru.resilience.cooldown import CooldownTracker
from lamaru.router.models import RouterScoringWeights
from lamaru.router.router import ModelRouter


def setup_test_environment():
    """Helper to assemble a realistic multi-provider, multi-model, multi-key test fixture."""
    model_reg = ModelRegistry()
    provider_reg = ProviderRegistry()
    key_mgr = APIKeyManager()
    cooldown = CooldownTracker()
    circuit_breakers = {}

    # 1. Google Provider
    p_google = MockProvider(provider_id="google")
    provider_reg.register(p_google)

    # Models
    m_flash = Model(
        provider="google",
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        capabilities={ModelCapability.CODING, ModelCapability.FAST, ModelCapability.TOOL_CALLING},
        context_window=1000000,
        priority=8,
    )
    m_pro = Model(
        provider="google",
        model_id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        capabilities={
            ModelCapability.CODING,
            ModelCapability.REASONING,
            ModelCapability.TOOL_CALLING,
            ModelCapability.LONG_CONTEXT,
        },
        context_window=2000000,
        priority=10,
    )
    model_reg.register(m_flash)
    model_reg.register(m_pro)

    # Keys for Google
    key_mgr.register_key(
        APIKeyMetadata(key_id="google-key-01", provider="google", secret_ref=SecretReference.from_raw("k1"))
    )
    key_mgr.register_key(
        APIKeyMetadata(key_id="google-key-02", provider="google", secret_ref=SecretReference.from_raw("k2"))
    )

    # 2. Local Provider
    p_local = MockProvider(provider_id="local")
    provider_reg.register(p_local)
    m_local = Model(
        provider="local",
        model_id="local-fast",
        display_name="Local Fast",
        capabilities={ModelCapability.FAST},
        context_window=8000,
        priority=3,
    )
    model_reg.register(m_local)

    circuit_breakers["google:gemini-2.5-pro"] = CircuitBreaker("google:gemini-2.5-pro", failure_threshold=2)

    router = ModelRouter(
        model_registry=model_reg,
        provider_registry=provider_reg,
        key_manager=key_mgr,
        cooldown_tracker=cooldown,
        circuit_breakers=circuit_breakers,
    )

    return router, model_reg, key_mgr, cooldown, circuit_breakers


def test_routing_pipeline_capability_filtering():
    router, _, _, _, _ = setup_test_environment()

    # Task requiring reasoning and tools
    task = TaskRequirement(task_type="coding", requires_tools=True, requires_reasoning=True)
    res = router.route(task)

    assert res.has_candidate is True
    # Only gemini-2.5-pro has reasoning
    assert res.selected_candidate.model_id == "gemini-2.5-pro"
    # Both key-01 and key-02 are ranked
    assert len(res.ranked_candidates) == 2
    for c in res.ranked_candidates:
        assert c.model_id == "gemini-2.5-pro"


def test_dynamic_scoring_task_affinity():
    router, _, _, _, _ = setup_test_environment()

    # Task 1: Complex reasoning coding task -> pro should win
    task_complex = TaskRequirement(task_type="coding", requires_tools=True)
    res1 = router.route(task_complex)
    # Pro has priority 10 vs Flash 8
    assert res1.selected_candidate.model_id == "gemini-2.5-pro"

    # Task 2: Fast chat task -> flash or local should get fast bonus
    task_fast = TaskRequirement(task_type="fast", requires_tools=False)
    res2 = router.route(task_fast)
    # gemini-flash has FAST capability and higher priority than local
    assert res2.selected_candidate.model_id == "gemini-2.5-flash"


def test_cooldown_candidate_automatically_removed():
    router, _, key_mgr, cooldown, _ = setup_test_environment()
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)

    task = TaskRequirement(task_type="coding", requires_tools=True, requires_reasoning=True)

    # Initial routing selects key-01
    res1 = router.route(task, now=now)
    assert res1.selected_candidate.key_id == "google-key-01"

    # Put google-key-01 in cooldown
    key_mgr.record_key_failure("google-key-01", error_msg="Rate limit", cooldown_seconds=60, now=now)

    # Next route dynamically selects google-key-02 without any hard-coded if/else
    res2 = router.route(task, now=now)
    assert res2.selected_candidate.key_id == "google-key-02"
    assert "google-key-01" not in [c.key_id for c in res2.ranked_candidates]


def test_circuit_breaker_open_removes_model():
    router, _, _, _, circuit_breakers = setup_test_environment()
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)

    # Trip circuit breaker for gemini-2.5-pro
    cb = circuit_breakers["google:gemini-2.5-pro"]
    cb.record_failure(now=now)
    cb.record_failure(now=now)
    assert cb.state.value == "OPEN"

    # Task requires coding and tools (both flash and pro have tools)
    task = TaskRequirement(task_type="coding", requires_tools=True)
    res = router.route(task, now=now)

    # pro is blocked by circuit breaker, so router dynamically chooses flash
    assert res.selected_candidate.model_id == "gemini-2.5-flash"
    assert "gemini-2.5-pro" not in [c.model_id for c in res.ranked_candidates]


def test_exclude_candidates_for_fallback():
    router, _, _, _, _ = setup_test_environment()
    task = TaskRequirement(task_type="coding", requires_tools=True, requires_reasoning=True)

    # Initial best
    res1 = router.route(task)
    best_key = res1.selected_candidate.target_key

    # Route again excluding the best candidate
    res2 = router.route(task, exclude_candidates={best_key})
    assert res2.has_candidate is True
    assert res2.selected_candidate.target_key != best_key
    assert res2.selected_candidate.key_id == "google-key-02"
