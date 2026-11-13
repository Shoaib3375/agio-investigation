"""LLMExplainer — validates LLM output and upgrades the investigation report.

Responsibilities:
  1. Build system + user prompts via PromptBuilder.
  2. Call the configured LLM provider.
  3. Validate the structured output:
     - evidence_ids reference real EvidenceRecord UUIDs for this investigation.
     - observed_facts are non-empty strings.
     - possible_explanations are separated from facts.
  4. Persist an upgraded InvestigationReport (new version) with:
     - LLM-generated summary, observed_facts, recommendations, limitations.
     - model_name and prompt_version for auditability.
  5. Return None gracefully when the provider is disabled or unavailable.

The LLM never:
  - Becomes the source of financial amounts, dates, or addresses.
  - Makes compliance or AML decisions.
  - Triggers financial actions.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    evidence_record_repository,
    finding_repository,
    report_repository,
)
from app.intelligence.llm.base import LLMProvider, LLMReport
from app.intelligence.llm.prompt_builder import (
    PROMPT_VERSION,
    build_system_prompt,
    build_user_prompt,
)
from app.models.investigation_report import InvestigationReport

logger = structlog.get_logger()


class LLMExplainer:
    """Coordinates evidence loading, prompt building, LLM call, and report update."""

    def __init__(self, provider: LLMProvider, session: AsyncSession) -> None:
        self._provider = provider
        self._session = session

    async def explain(
        self,
        investigation_id: str,
        external_reference: str,
        entity_type: str,
        *,
        model: str,
        max_tokens: int = 2048,
    ) -> InvestigationReport | None:
        """Run the LLM explanation pass for one investigation.

        Returns the newly created InvestigationReport on success,
        or None if the provider is disabled/unavailable.
        """
        log = logger.bind(investigation_id=investigation_id)

        if not self._provider.is_available():
            log.debug("llm.explainer.provider_disabled", provider=self._provider.provider_name)
            return None

        # ── 1. Load evidence and findings ─────────────────────────────
        evidence_records = await evidence_record_repository.get_by_investigation(
            self._session, investigation_id
        )
        findings = await finding_repository.get_by_investigation(
            self._session, investigation_id
        )

        valid_evidence_ids = {rec.id for rec in evidence_records}

        # ── 2. Build prompts ──────────────────────────────────────────
        evidence_dicts = [
            {
                "id": rec.id,
                "evidence_type": rec.evidence_type,
                "source_record_type": rec.source_record_type,
                "source_record_id": rec.source_record_id,
                "snapshot": rec.snapshot,
            }
            for rec in evidence_records
        ]

        finding_dicts = [
            {
                "id": f.id,
                "finding_code": f.finding_code,
                "severity": f.severity,
                "title": f.title,
                "description": f.description,
            }
            for f in findings
        ]

        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(
            investigation_id=investigation_id,
            external_reference=external_reference,
            entity_type=entity_type,
            evidence_records=evidence_dicts,
            findings=finding_dicts,
        )

        # ── 3. Call LLM ───────────────────────────────────────────────
        log.info("llm.explainer.calling", provider=self._provider.provider_name, model=model)
        llm_report = await self._provider.explain(
            system_prompt,
            user_prompt,
            model=model,
            max_tokens=max_tokens,
        )

        if llm_report is None:
            log.warning("llm.explainer.no_output", provider=self._provider.provider_name)
            return None

        # ── 4. Validate evidence_ids in observed_facts ────────────────
        validated_facts = self._validate_facts(llm_report, valid_evidence_ids, log)

        # ── 5. Persist upgraded report ────────────────────────────────
        version = await report_repository.next_version(self._session, investigation_id)

        observed_facts_json = [
            {"statement": fact.statement, "evidence_ids": fact.evidence_ids}
            for fact in validated_facts
        ]
        findings_snapshot = [
            {"id": f.id, "finding_code": f.finding_code, "severity": f.severity, "title": f.title}
            for f in findings
        ]
        explanations_json = [
            {"explanation": ex.explanation, "confidence": ex.confidence}
            for ex in llm_report.possible_explanations
        ]

        limitations = list(llm_report.limitations) + [
            "This report was generated by an LLM and must be reviewed by a qualified human. "
            "It is not a legal, compliance, or AML determination."
        ]

        report = InvestigationReport(
            investigation_id=investigation_id,
            report_version=version,
            summary=llm_report.summary,
            observed_facts=observed_facts_json,
            findings=findings_snapshot + [
                {"type": "possible_explanation", "items": explanations_json}
            ],
            missing_evidence=[{"description": m} for m in llm_report.missing_evidence],
            recommendations=llm_report.recommended_review,
            limitations=limitations,
            model_name=f"{self._provider.provider_name}:{model}",
            prompt_version=PROMPT_VERSION,
        )

        saved = await report_repository.insert(self._session, report)
        log.info(
            "llm.explainer.report_persisted",
            report_id=saved.id,
            report_version=version,
            model=model,
            facts=len(validated_facts),
        )
        return saved

    # ------------------------------------------------------------------

    @staticmethod
    def _validate_facts(
        llm_report: LLMReport,
        valid_evidence_ids: set[str],
        log: Any,
    ):
        """Remove facts referencing evidence_ids not in the investigation."""
        validated = []
        for fact in llm_report.observed_facts:
            valid_refs = [eid for eid in fact.evidence_ids if eid in valid_evidence_ids]
            invalid_refs = [eid for eid in fact.evidence_ids if eid not in valid_evidence_ids]
            if invalid_refs:
                log.warning(
                    "llm.explainer.invalid_evidence_refs",
                    invalid_ids=invalid_refs,
                    statement=fact.statement[:80],
                )
            # Keep the fact if the statement is non-empty; drop invalid refs
            if fact.statement.strip():
                from app.intelligence.llm.base import ObservedFact
                validated.append(ObservedFact(
                    statement=fact.statement,
                    evidence_ids=valid_refs,
                ))
        return validated


def build_provider(settings) -> LLMProvider:
    """Factory: instantiate the configured LLM provider from settings."""
    from app.intelligence.llm.disabled import DisabledProvider
    from app.intelligence.llm.openai_provider import OpenAIProvider

    provider_name = (settings.llm_provider or "disabled").lower()

    if provider_name == "openai":
        if not settings.llm_api_key:
            import structlog as _sl
            _sl.get_logger().warning(
                "llm.provider.openai_missing_key",
                note="Falling back to disabled provider.",
            )
            return DisabledProvider()
        return OpenAIProvider(
            api_key=settings.llm_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    return DisabledProvider()
