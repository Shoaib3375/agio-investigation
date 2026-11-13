"""Disabled LLM provider — always returns None gracefully.

Used when LLM_PROVIDER=disabled (the default). The investigation pipeline
continues with deterministic findings only; no LLM call is made.
"""

from __future__ import annotations

from app.intelligence.llm.base import LLMProvider, LLMReport


class DisabledProvider(LLMProvider):
    """No-op provider — always returns None without making any network call."""

    @property
    def provider_name(self) -> str:
        return "disabled"

    def is_available(self) -> bool:
        return False

    async def explain(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        max_tokens: int = 2048,
    ) -> LLMReport | None:
        return None
