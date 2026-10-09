"""ContextManager: Token budgeting, context tier ranking, and compression."""

import logging
from typing import Dict, List, Optional, Tuple

from lamaru.context.models import (
    AssembledContext,
    ContextBudget,
    ContextItem,
    ContextTier,
)
from lamaru.domain.messages import Message, MessageRole

logger = logging.getLogger(__name__)


class ContextManager:
    """Manages context assembly, token budgeting, tier prioritization, and compression.

    Rules:
    - Never automatically dumps the entire repository.
    - Always preserves current task (Tier 1) with highest priority.
    - Tracks input estimate, output budget, total context, and model context limit.
    - Compresses older conversation and truncates oversized files when exceeding budget.
    """

    def __init__(self, chars_per_token: float = 3.8) -> None:
        self.chars_per_token = max(1.0, chars_per_token)

    def estimate_tokens(self, text: Optional[str]) -> int:
        """Estimate token count for a text string using character-based heuristic."""
        if not text:
            return 0
        return max(1, int(len(text) / self.chars_per_token))

    def estimate_message_tokens(self, message: Message) -> int:
        """Estimate total tokens for a single Message object."""
        tokens = self.estimate_tokens(message.content)
        for tc in message.tool_calls:
            tokens += self.estimate_tokens(tc.name)
            tokens += self.estimate_tokens(str(tc.arguments))
        return tokens

    def calculate_budget(
        self,
        model_context_limit: int,
        output_budget: int = 4096,
        safety_margin_ratio: float = 0.05,
    ) -> ContextBudget:
        """Calculate and initialize a ContextBudget for a given model limit."""
        safety_margin = int(model_context_limit * safety_margin_ratio)
        max_input = max(512, model_context_limit - output_budget - safety_margin)

        return ContextBudget(
            model_context_limit=model_context_limit,
            output_budget=output_budget,
            max_input_budget=max_input,
            input_estimate=0,
            total_context=output_budget,
        )

    def compress_history(
        self,
        messages: List[Message],
        budget_tokens: int,
    ) -> Tuple[List[Message], bool]:
        """Compress older messages if they exceed the allocated token budget.

        Preserves the latest messages while summarizing older turns.
        """
        total_tokens = sum(self.estimate_message_tokens(m) for m in messages)
        if total_tokens <= budget_tokens:
            return messages, False

        # Keep the most recent messages that fit within budget
        preserved: List[Message] = []
        accumulated_tokens = 0
        is_compressed = False

        # Walk backwards from most recent message
        for m in reversed(messages):
            m_tokens = self.estimate_message_tokens(m)
            if accumulated_tokens + m_tokens <= budget_tokens:
                preserved.insert(0, m)
                accumulated_tokens += m_tokens
            else:
                is_compressed = True
                break

        # If compression occurred and older messages were dropped, prepend summary marker
        if is_compressed:
            summary_msg = Message(
                role=MessageRole.SYSTEM,
                content="[Older conversation history summarized and truncated to preserve context budget]",
            )
            preserved.insert(0, summary_msg)

        return preserved, True

    def assemble_context(
        self,
        task: str,
        relevant_files: Optional[Dict[str, str]] = None,
        project_knowledge: Optional[str] = None,
        messages: Optional[List[Message]] = None,
        model_context_limit: int = 32000,
        output_budget: int = 4096,
    ) -> AssembledContext:
        """Assemble a complete, prioritized, and budgeted context for model execution."""
        budget = self.calculate_budget(
            model_context_limit=model_context_limit,
            output_budget=output_budget,
        )

        items: List[ContextItem] = []
        is_compressed = False
        consumed_input = 0

        # Tier 1: Current Task (Mandatory, highest priority)
        task_tokens = self.estimate_tokens(task)
        task_item = ContextItem(
            tier=ContextTier.TIER_1_TASK,
            source="user_task",
            content=task,
            estimated_tokens=task_tokens,
            priority=100,
        )
        items.append(task_item)
        consumed_input += task_tokens

        # Tier 3: Project Knowledge (Conventions, Architecture)
        if project_knowledge and (consumed_input + self.estimate_tokens(project_knowledge) <= budget.max_input_budget):
            pk_tokens = self.estimate_tokens(project_knowledge)
            items.append(
                ContextItem(
                    tier=ContextTier.TIER_3_KNOWLEDGE,
                    source="project_knowledge",
                    content=project_knowledge,
                    estimated_tokens=pk_tokens,
                    priority=70,
                )
            )
            consumed_input += pk_tokens

        # Tier 2: Relevant Files (Targeted code sections)
        included_files: List[str] = []
        file_map = relevant_files or {}
        for f_path, f_content in file_map.items():
            f_tokens = self.estimate_tokens(f_content)
            remaining_for_files = budget.max_input_budget - consumed_input

            if remaining_for_files <= 100:
                is_compressed = True
                break

            if f_tokens <= remaining_for_files:
                items.append(
                    ContextItem(
                        tier=ContextTier.TIER_2_FILES,
                        source=f_path,
                        content=f_content,
                        estimated_tokens=f_tokens,
                        priority=80,
                    )
                )
                consumed_input += f_tokens
                included_files.append(f_path)
            else:
                # Truncate large file to fit available budget
                allowed_chars = int(remaining_for_files * self.chars_per_token * 0.9)
                truncated_content = f_content[:allowed_chars] + "\n... [Content truncated due to context budget]"
                trunc_tokens = self.estimate_tokens(truncated_content)

                items.append(
                    ContextItem(
                        tier=ContextTier.TIER_2_FILES,
                        source=f_path,
                        content=truncated_content,
                        estimated_tokens=trunc_tokens,
                        priority=80,
                    )
                )
                consumed_input += trunc_tokens
                included_files.append(f_path)
                is_compressed = True
                break

        # Tier 4 & 5: Conversation Messages
        raw_messages = messages or []
        remaining_for_messages = max(0, budget.max_input_budget - consumed_input)
        final_messages, msg_compressed = self.compress_history(
            raw_messages, remaining_for_messages
        )
        if msg_compressed:
            is_compressed = True

        msg_tokens = sum(self.estimate_message_tokens(m) for m in final_messages)
        consumed_input += msg_tokens

        # Final budget update
        budget.input_estimate = consumed_input
        budget.total_context = consumed_input + budget.output_budget

        # Prepare system instruction from knowledge and files
        sys_parts = []
        if project_knowledge:
            sys_parts.append(f"## Project Knowledge\n{project_knowledge}")
        if included_files:
            file_summaries = []
            for item in items:
                if item.tier == ContextTier.TIER_2_FILES:
                    file_summaries.append(f"### {item.source}\n```\n{item.content}\n```")
            sys_parts.append("## Relevant Files\n" + "\n\n".join(file_summaries))

        system_instruction = "\n\n".join(sys_parts)

        return AssembledContext(
            budget=budget,
            items=items,
            system_instruction=system_instruction,
            messages=final_messages,
            relevant_files=included_files,
            is_compressed=is_compressed,
        )
