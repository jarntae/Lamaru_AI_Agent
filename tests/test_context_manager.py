"""Tests for ContextManager, token budgeting, tier ranking, and compression."""

import pytest

from lamaru.context.manager import ContextManager
from lamaru.context.models import ContextBudget, ContextItem, ContextTier
from lamaru.domain.messages import Message, MessageRole, ToolCall


def test_token_estimation():
    mgr = ContextManager(chars_per_token=4.0)
    assert mgr.estimate_tokens("") == 0
    assert mgr.estimate_tokens(None) == 0
    assert mgr.estimate_tokens("abcd") == 1
    assert mgr.estimate_tokens("abcdefgh") == 2

    # Message with tool calls
    msg = Message(
        role=MessageRole.ASSISTANT,
        content="Hello world!",
        tool_calls=[
            ToolCall(id="call_1", name="read_file", arguments={"path": "main.py"})
        ],
    )
    tokens = mgr.estimate_message_tokens(msg)
    assert tokens > mgr.estimate_tokens("Hello world!")


def test_budget_calculation():
    mgr = ContextManager()
    budget = mgr.calculate_budget(
        model_context_limit=32000,
        output_budget=4000,
        safety_margin_ratio=0.1,  # 3200
    )
    # max_input = 32000 - 4000 - 3200 = 24800
    assert budget.model_context_limit == 32000
    assert budget.output_budget == 4000
    assert budget.max_input_budget == 24800
    assert budget.input_estimate == 0
    assert budget.total_context == 4000
    assert budget.is_within_limit is True
    assert budget.remaining_budget == 24800


def test_history_compression_when_exceeding_budget():
    mgr = ContextManager(chars_per_token=4.0)

    # 10 messages of 40 characters each = 10 tokens each -> 100 tokens total
    messages = [
        Message(role=MessageRole.USER, content=f"Message index {i:03d}" + "x" * 23)
        for i in range(10)
    ]
    # Budget only 35 tokens -> should preserve ~3 most recent messages + 1 summary message
    preserved, compressed = mgr.compress_history(messages, budget_tokens=35)

    assert compressed is True
    assert len(preserved) < len(messages)
    # The first message in preserved should be the summary marker
    assert preserved[0].role == MessageRole.SYSTEM
    assert "truncated" in preserved[0].content
    # The last message should be the original last message
    assert preserved[-1].content == messages[-1].content


def test_history_compression_within_budget():
    mgr = ContextManager(chars_per_token=4.0)
    messages = [
        Message(role=MessageRole.USER, content="Short 1"),
        Message(role=MessageRole.ASSISTANT, content="Short 2"),
    ]
    preserved, compressed = mgr.compress_history(messages, budget_tokens=1000)
    assert compressed is False
    assert len(preserved) == 2


def test_assemble_context_full_flow():
    mgr = ContextManager(chars_per_token=4.0)

    task = "Implement the new feature X according to specifications."
    files = {
        "src/core.py": "def core_function():\n    return 42\n",
        "README.md": "# Project Title\nDescription of project.\n",
    }
    knowledge = "Architecture: Clean architecture. Rule: No hardcoding."
    messages = [
        Message(role=MessageRole.USER, content="Start working on feature X."),
        Message(role=MessageRole.ASSISTANT, content="Understood, I am reviewing files."),
    ]

    assembled = mgr.assemble_context(
        task=task,
        relevant_files=files,
        project_knowledge=knowledge,
        messages=messages,
        model_context_limit=16000,
        output_budget=2000,
    )

    assert assembled.budget.is_within_limit is True
    assert assembled.budget.input_estimate > 0
    assert assembled.budget.total_context == assembled.budget.input_estimate + 2000
    assert assembled.is_compressed is False
    assert len(assembled.relevant_files) == 2
    assert "src/core.py" in assembled.relevant_files
    assert "README.md" in assembled.relevant_files

    # Tier 1 task must be present
    task_items = [it for it in assembled.items if it.tier == ContextTier.TIER_1_TASK]
    assert len(task_items) == 1
    assert task_items[0].content == task

    # Knowledge tier must be present
    knowledge_items = [it for it in assembled.items if it.tier == ContextTier.TIER_3_KNOWLEDGE]
    assert len(knowledge_items) == 1

    # System instruction must include knowledge and files
    assert "Project Knowledge" in assembled.system_instruction
    assert "src/core.py" in assembled.system_instruction


def test_assemble_context_file_truncation_when_tight_budget():
    mgr = ContextManager(chars_per_token=4.0)

    task = "Inspect large file"
    # Create a massive file content (e.g. 5,000 chars ~ 1250 tokens)
    large_content = "line of code\n" * 400
    files = {"big_file.py": large_content}

    # Set very small model limit to force truncation
    # Limit: 800, output: 200 -> max_input ~ 560
    assembled = mgr.assemble_context(
        task=task,
        relevant_files=files,
        model_context_limit=800,
        output_budget=200,
    )

    assert assembled.is_compressed is True
    file_items = [it for it in assembled.items if it.tier == ContextTier.TIER_2_FILES]
    assert len(file_items) == 1
    assert "... [Content truncated due to context budget]" in file_items[0].content
    assert assembled.budget.is_within_limit is True
