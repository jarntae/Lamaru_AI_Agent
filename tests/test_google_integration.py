"""Opt-in integration tests for GoogleProvider requiring real GEMINI_API_KEY.

These tests are skipped by default unless GEMINI_API_KEY is configured in the environment.
"""

import asyncio
import os
import pytest

from lamaru.domain.messages import AIRequest, Message, MessageRole
from lamaru.providers.google.provider import GoogleProvider

API_KEY = os.environ.get("GEMINI_API_KEY")


@pytest.mark.skipif(not API_KEY, reason="GEMINI_API_KEY environment variable not set")
def test_google_provider_real_api_integration():
    async def _test():
        provider = GoogleProvider(api_key=API_KEY)

        # Real model discovery
        models = await provider.list_models()
        assert len(models) > 0

        # Real completion test
        req = AIRequest(
            messages=[Message(role=MessageRole.USER, content="Reply with one word: ping")],
            model="gemini-2.5-flash",
        )
        res = await provider.generate(req)
        assert res.content is not None
        assert len(res.content) > 0

    asyncio.run(_test())
