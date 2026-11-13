"""LLM provider base interface and structured output schema.

Design principles:
  - The LLM never asserts financial facts — it explains and summarises.
  - Every factual statement in LLMReport.observed_facts must reference
    at least one evidence_id from the investigation's EvidenceRecord rows.
  - LLMReport.possible_explanations are hypotheses, not verified facts.
  - confidence strings are qualitative ("low"/"medium"/"high"), not probabilities.
  - The LLM is not an AML provider and cannot make compliance decisions.
  - Prompt-injection defenses: evidence is inserted as data blocks, not
    as free-form instructions. The system prompt explicitly forbids the
    LLM from acting on instructions found inside evidence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Structured LLM output schemas
# ---------------------------------------------------------------------------


class ObservedFact(BaseModel):
    """A fact grounded in one or more evidence records."""
    statement: str
    evidence_ids: list[str]  # UUIDs of EvidenceRecord rows that support this

    @field_validator("evidence_ids")
    @classmethod
    def _must_have_evidence(cls, v: list[str]) -> list[str]:
        # Warn but do not reject — validation happens in LLMExplainer
        return v


class PossibleExplanation(BaseModel):
    """A hypothesis, not a verified fact."""
    explanation: str
    confidence: str  # "low" | "medium" | "high" — qualitative only


class LLMReport(BaseModel):
    """Structured output from an LLM explanation pass.

    This schema matches the spec section 11.4 exactly.
    It is validated by LLMExplainer before being persisted.
    """
    summary: str
    observed_facts: list[ObservedFact]
    possible_explanations: list[PossibleExplanation]
    missing_evidence: list[str]
    recommended_review: list[str]
    limitations: list[str]

    @field_validator("summary")
    @classmethod
    def _summary_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary must not be empty")
        return v.strip()

    @field_validator("possible_explanations")
    @classmethod
    def _confidence_values(cls, v: list[PossibleExplanation]) -> list[PossibleExplanation]:
        valid = {"low", "medium", "high"}
        for item in v:
            if item.confidence not in valid:
                raise ValueError(
                    f"confidence must be one of {valid}, got '{item.confidence}'"
                )
        return v


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract async LLM provider.

    Implementations must return an LLMReport or None.
    None means the provider is disabled or unavailable — the investigation
    continues without an LLM explanation.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable identifier for this provider (e.g. 'openai', 'disabled')."""

    @abstractmethod
    async def explain(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        max_tokens: int = 2048,
    ) -> LLMReport | None:
        """Call the LLM and return a parsed LLMReport, or None on failure.

        Parameters
        ----------
        system_prompt:
            Static system context — sets role, constraints, output schema.
            Never contains evidence data.
        user_prompt:
            Investigation context including evidence data blocks.
            Evidence is inserted as labelled data, not as instructions.
        model:
            Model identifier (provider-specific).
        max_tokens:
            Maximum tokens to generate.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider is configured and can be called."""
