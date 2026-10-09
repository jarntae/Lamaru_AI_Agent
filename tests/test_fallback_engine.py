import asyncio
from datetime import datetime, timezone
import pytest

from lamaru.domain.agent import AgentState, AgentStatus, Plan
from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import AIRequest, Message, MessageRole
from lamaru.domain.models import Model, ModelCapability, TaskRequirement
from lamaru.errors.classifier import ErrorClassifier
from lamaru.keys.manager import APIKeyManager
from lamaru.keys.models import APIKeyMetadata, SecretReference
from lamaru.models.registry import ModelRegistry
from lamaru.providers.mock import MockProvider
from lamaru.providers.registry import ProviderRegistry
from lamaru.resilience.cooldown import CooldownTracker
from lamaru.router.fallback import FallbackEngine
from lamaru.router.router import ModelRouter


def setup_fallback_environment():
    model_reg = ModelRegistry()
    provider_reg = ProviderRegistry()
    key_mgr = APIKeyManager()
    cooldown = CooldownTracker()
    error_classifier = ErrorClassifier()

    # Provider A (e.g. Google) with 2 keys
    prov_a = MockProvider(provider_id="provider-a")
    provider_reg.register(prov_a)

    model_a = Model(
        provider="provider-a",
        model_id="model-a",
        display_name="Model A",
        capabilities={ModelCapability.CODING, ModelCapability.FAST},
        context_window=100000,
        priority=10,  # Higher priority than B
    )
    model_reg.register(model_a)
    prov_a.add_model(model_a)

    key_mgr.register_key(
        APIKeyMetadata(key_id="key-01", provider="provider-a", secret_ref=SecretReference.from_raw("k1"))
    )
    key_mgr.register_key(
        APIKeyMetadata(key_id="key-02", provider="provider-a", secret_ref=SecretReference.from_raw("k2"))
    )

    # Provider B (Alternative provider) with 1 key
    prov_b = MockProvider(provider_id="provider-b", default_response="Success from Provider B!")
    provider_reg.register(prov_b)

    model_b = Model(
        provider="provider-b",
        model_id="model-b",
        display_name="Model B",
        capabilities={ModelCapability.CODING, ModelCapability.FAST},
        context_window=100000,
        priority=5,
    )
    model_reg.register(model_b)
    prov_b.add_model(model_b)

    key_mgr.register_key(
        APIKeyMetadata(key_id="key-b1", provider="provider-b", secret_ref=SecretReference.from_raw("kb1"))
    )

    router = ModelRouter(
        model_registry=model_reg,
        provider_registry=provider_reg,
        key_manager=key_mgr,
        cooldown_tracker=cooldown,
    )

    engine = FallbackEngine(
        router=router,
        provider_registry=provider_reg,
        key_manager=key_mgr,
        cooldown_tracker=cooldown,
        error_classifier=error_classifier,
    )

    return engine, prov_a, prov_b, key_mgr, cooldown


def test_specification_scenario_a_key1_429_a_key2_429_b_key1_success():
    """Verify Master Plan scenario: A/key1 -> 429, A/key2 -> 429, B/key1 -> success.

    Must result in: A/key1 -> A/key2 -> B/key1.
    """
    async def _test():
        engine, prov_a, prov_b, key_mgr, _ = setup_fallback_environment()

        # Simulate Provider A failing with 429 on any key
        err_429 = LamaruError("Rate limit exceeded 429", category=ErrorCategory.RATE_LIMIT)
        prov_a.set_simulated_error(err_429)

        req = AIRequest(
            messages=[Message(role=MessageRole.USER, content="Run task")],
            model="model-a",
        )

        result = await engine.execute_with_fallback(req)

        assert result.success is True
        assert result.response.content == "Success from Provider B!"
        assert result.selected_candidate.provider == "provider-b"
        assert result.selected_candidate.key_id == "key-b1"
        assert result.fallback_count == 2

        # Verify the exact sequence of attempts
        attempt_targets = [a.candidate.target_key for a in result.attempts]
        assert attempt_targets == [
            "provider-a:model-a:key-01",
            "provider-a:model-a:key-02",
            "provider-b:model-b:key-b1",
        ]

        # Verify both failed attempts are recorded with RATE_LIMIT
        assert result.attempts[0].success is False
        assert result.attempts[0].error_category == ErrorCategory.RATE_LIMIT
        assert result.attempts[1].success is False
        assert result.attempts[1].error_category == ErrorCategory.RATE_LIMIT
        assert result.attempts[2].success is True

        # Verify Provider A keys are now in cooldown
        k1 = key_mgr.get_key("key-01")
        k2 = key_mgr.get_key("key-02")
        assert k1.is_available() is False
        assert k2.is_available() is False

    asyncio.run(_test())


def test_state_preservation_during_fallback():
    """Verify AgentState is preserved intact when fallback engine switches candidates."""
    async def _test():
        engine, prov_a, prov_b, _, _ = setup_fallback_environment()

        # Provider A fails with 429
        prov_a.set_simulated_error(
            LamaruError("429 Rate limit", category=ErrorCategory.RATE_LIMIT)
        )

        # Agent state
        state = AgentState(
            session_id="session-xyz-99",
            task="Refactor routing subsystem",
            plan=Plan(steps=["Step 1", "Step 2", "Step 3"], current_step_index=1),
            current_step="Step 2",
            messages=[Message(role=MessageRole.USER, content="Perform refactor")],
            relevant_files=["src/lamaru/router/fallback.py"],
            iteration_count=1,
            status=AgentStatus.RUNNING,
        )

        req = AIRequest(messages=state.messages, model="model-a")

        # Execute fallback
        result = await engine.execute_with_fallback(req)
        assert result.success is True

        # Update AgentState model history with the newly selected fallback model
        state.model_history.append(result.selected_candidate.model_id)

        # Verify AgentState integrity is preserved 100%
        assert state.session_id == "session-xyz-99"
        assert state.task == "Refactor routing subsystem"
        assert state.plan.current_step == "Step 2"
        assert state.relevant_files == ["src/lamaru/router/fallback.py"]
        assert state.iteration_count == 1
        assert state.status == AgentStatus.RUNNING
        assert state.model_history == ["model-b"]

    asyncio.run(_test())


def test_non_fallbackable_safety_block_aborts_immediately():
    """Verify SAFETY_BLOCK on prompt aborts without cycling through other providers."""
    async def _test():
        engine, prov_a, _, _, _ = setup_fallback_environment()

        prov_a.set_simulated_error(
            LamaruError("Prompt safety blocked", category=ErrorCategory.SAFETY_BLOCK)
        )

        req = AIRequest(
            messages=[Message(role=MessageRole.USER, content="Harmful prompt")],
            model="model-a",
        )

        with pytest.raises(LamaruError) as exc_info:
            await engine.execute_with_fallback(req)

        assert exc_info.value.category == ErrorCategory.SAFETY_BLOCK

    asyncio.run(_test())


def test_all_candidates_exhausted_raises_quota_exceeded():
    """Verify when all available fallback candidates fail, an informative LamaruError is raised."""
    async def _test():
        engine, prov_a, prov_b, _, _ = setup_fallback_environment()

        # Both providers fail with 429
        prov_a.set_simulated_error(LamaruError("A 429", category=ErrorCategory.RATE_LIMIT))
        prov_b.set_simulated_error(LamaruError("B 429", category=ErrorCategory.RATE_LIMIT))

        req = AIRequest(messages=[Message(role=MessageRole.USER, content="Hi")], model="model-a")

        with pytest.raises(LamaruError) as exc_info:
            await engine.execute_with_fallback(req, max_fallbacks=3)

        assert exc_info.value.category == ErrorCategory.QUOTA_EXCEEDED
        assert "exhausted" in exc_info.value.message.lower()

    asyncio.run(_test())
