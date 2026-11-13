"""InvestigationOrchestrator — wires event_inbox → investigation → evidence collection → rule engine.

Called asynchronously after a RabbitMQ message is ACKed. The orchestrator:
  1. Loads the EventInbox record.
  2. Uses InvestigationService to get or create an investigation.
  3. Transitions it to "collecting".
  4. Runs EvidenceCollector to retrieve and persist all relevant evidence.
  5. Runs the RuleEngine to evaluate deterministic rules against evidence.
  6. Builds a full report (observed_facts + findings + missing_evidence) in the DB.
  7. Transitions to "completed" (no missing evidence) or "review_required".
  8. On any unhandled failure: transitions to "failed" and records the error.

The orchestrator never modifies Laravel state.
"""

from __future__ import annotations

import traceback
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.database.repositories import (
    event_inbox_repository,
    investigation_repository,
    report_repository,
)
from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.evidence_client import EvidenceClient
from app.investigation.evidence_collector import CollectionResult, EvidenceCollector
from app.investigation.investigation_service import InvestigationService
from app.investigation.rules.engine import RuleEngine
from app.models.investigation_finding import InvestigationFinding
from app.models.investigation_report import InvestigationReport

logger = structlog.get_logger()


class InvestigationOrchestrator:
    """Coordinates investigation creation and evidence collection for one event."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def process_event(self, event_id_in_inbox: str) -> None:
        """Process one event_inbox record end-to-end.

        Each phase uses its own session so a failure in evidence collection
        does not roll back the investigation creation.
        """
        log = logger.bind(inbox_event_id=event_id_in_inbox)

        # ── Phase 1: load event, create/correlate investigation ────────────
        async with self._session_factory() as session:
            event = await self._load_event(session, event_id_in_inbox)
            if event is None:
                log.warning("orchestrator.event_not_found")
                return

            await event_inbox_repository.mark_processing(session, event.id)

            svc = InvestigationService(session)
            investigation, created = await svc.get_or_create_for_event(event)

            if investigation is None:
                # Event does not trigger an investigation (non-triggering status etc.)
                await event_inbox_repository.mark_processed(session, event.id)
                await session.commit()
                log.debug("orchestrator.no_investigation_needed", event_id=event.event_id)
                return

            await session.commit()

        log = log.bind(
            investigation_id=investigation.id,
            entity_type=investigation.entity_type,
            external_reference=investigation.external_reference,
        )

        # ── Phase 2: transition to collecting ─────────────────────────────
        async with self._session_factory() as session:
            await investigation_repository.set_status(session, investigation.id, "collecting")
            await session.commit()

        # ── Phase 3: collect evidence ──────────────────────────────────────
        collection_result: CollectionResult | None = None
        try:
            collection_result = await self._collect_evidence(investigation, log)
        except Exception as exc:
            tb = traceback.format_exc()
            log.error("orchestrator.collection_failed", error=str(exc))
            async with self._session_factory() as session:
                await investigation_repository.set_status(
                    session,
                    investigation.id,
                    "failed",
                    error=f"{type(exc).__name__}: {str(exc)[:500]}",
                )
                await event_inbox_repository.mark_failed(
                    session,
                    event_id_in_inbox,
                    error=f"collection_failed: {str(exc)[:300]}",
                )
                await session.commit()
            return

        # ── Phase 4: run rule engine ───────────────────────────────────────
        findings: list[InvestigationFinding] = []
        try:
            async with self._session_factory() as session:
                await investigation_repository.set_status(session, investigation.id, "analyzing")
                engine = RuleEngine(session)
                findings = await engine.analyze(
                    investigation_id=investigation.id,
                    external_reference=investigation.external_reference,
                    entity_type=investigation.entity_type,
                )
                await session.commit()
                for f in findings:
                    from app.core.metrics import metrics as _m
                    _m.inc("findings", severity=f.severity)
        except Exception as exc:
            log.error("orchestrator.rule_engine_failed", error=str(exc))
            # Non-fatal: proceed to report with empty findings

        # ── Phase 5: LLM explanation (optional) ───────────────────────────
        try:
            async with self._session_factory() as session:
                from app.intelligence.llm.explainer import LLMExplainer, build_provider
                provider = build_provider(self._settings)
                if provider.is_available():
                    explainer = LLMExplainer(provider, session)
                    llm_report = await explainer.explain(
                        investigation_id=investigation.id,
                        external_reference=investigation.external_reference,
                        entity_type=investigation.entity_type,
                        model=self._settings.llm_model,
                        max_tokens=self._settings.llm_max_tokens,
                    )
                    if llm_report:
                        await session.commit()
                        log.info("orchestrator.llm_report_generated", report_id=llm_report.id)
        except Exception as exc:
            log.error("orchestrator.llm_failed", error=str(exc))
            # Non-fatal: investigation continues without LLM explanation

        # ── Phase 6: persist final report + mark complete ─────────────────
        async with self._session_factory() as session:
            await self._persist_final_report(
                session, investigation.id, collection_result, findings
            )
            status = "review_required" if (collection_result.missing or findings) else "completed"
            await investigation_repository.set_status(session, investigation.id, status)
            await event_inbox_repository.mark_processed(session, event_id_in_inbox)
            await session.commit()

        log.info(
            "orchestrator.done",
            status=status,
            collected=len(collection_result.collected),
            missing=len(collection_result.missing),
            findings=len(findings),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_event(self, session: AsyncSession, event_id: str):
        """Load an EventInbox by its DB UUID (not event_id string)."""
        from app.models.event_inbox import EventInbox
        from sqlalchemy import select

        result = await session.execute(
            select(EventInbox).where(EventInbox.id == event_id)
        )
        return result.scalars().first()

    async def _collect_evidence(self, investigation, log) -> CollectionResult:
        """Open a LaravelClient and run EvidenceCollector in its own session."""
        if not self._settings.laravel_internal_api_url or not self._settings.laravel_service_token:
            log.warning("orchestrator.laravel_not_configured")
            from app.investigation.evidence_collector import CollectionResult, MissingEvidence
            result = CollectionResult(investigation_id=investigation.id)
            result.missing.append(MissingEvidence(
                evidence_type="all",
                source_record_type="laravel_api",
                source_record_id=investigation.external_reference,
                reason="not_configured",
                detail="LARAVEL_INTERNAL_API_URL or LARAVEL_SERVICE_TOKEN not set",
            ))
            return result

        async with self._session_factory() as session:
            async with LaravelClient.from_settings(self._settings) as http_client:
                ec = EvidenceClient(http_client)
                collector = EvidenceCollector(ec, session)

                if investigation.entity_type == "order":
                    result = await collector.collect_for_order(
                        investigation_id=investigation.id,
                        order_uuid=investigation.external_reference,
                    )
                elif investigation.entity_type == "user":
                    # Parse user_id from external_reference (stored as string)
                    try:
                        user_id = int(investigation.external_reference)
                    except ValueError:
                        log.warning("orchestrator.invalid_user_id", ref=investigation.external_reference)
                        from app.investigation.evidence_collector import CollectionResult, MissingEvidence
                        result = CollectionResult(investigation_id=investigation.id)
                        result.missing.append(MissingEvidence(
                            evidence_type="all",
                            source_record_type="user",
                            source_record_id=investigation.external_reference,
                            reason="api_error",
                            detail="external_reference is not a valid user id",
                        ))
                        await session.commit()
                        return result
                    result = await collector.collect_for_user(
                        investigation_id=investigation.id,
                        user_id=user_id,
                    )
                else:
                    # For withdrawal / risk_case — collect user account context if available
                    from app.investigation.evidence_collector import CollectionResult
                    result = CollectionResult(investigation_id=investigation.id)
                    log.info(
                        "orchestrator.entity_type_no_collector",
                        entity_type=investigation.entity_type,
                    )

                await session.commit()
        return result

    async def _persist_final_report(
        self,
        session: AsyncSession,
        investigation_id: str,
        result: CollectionResult,
        findings: list[InvestigationFinding],
    ) -> None:
        """Persist a full report after evidence collection and rule evaluation."""
        version = await report_repository.next_version(session, investigation_id)

        observed_facts = [
            {
                "statement": f"Retrieved {len(result.collected)} evidence record(s).",
                "evidence_ids": [c.record_id for c in result.collected],
            }
        ]

        missing_evidence = [
            {
                "evidence_type": m.evidence_type,
                "source_record_type": m.source_record_type,
                "source_record_id": m.source_record_id,
                "reason": m.reason,
                "detail": m.detail,
            }
            for m in result.missing
        ]

        findings_snapshot = [
            {
                "id": f.id,
                "finding_code": f.finding_code,
                "category": f.category,
                "severity": f.severity,
                "title": f.title,
            }
            for f in findings
        ]

        limitations: list[str] = []
        if result.missing:
            limitations.append(
                f"{len(result.missing)} evidence item(s) could not be retrieved. "
                "Findings may be incomplete."
            )
        if not findings:
            limitations.append(
                "No deterministic rule findings were triggered. "
                "This does not confirm the transaction is anomaly-free."
            )
        limitations.append(
            "This report reflects deterministic rule evaluation only. "
            "LLM-based explanation is not yet enabled (PY-M07)."
        )

        recommendations: list[str] = []
        if result.missing:
            recommendations.append("Review and resolve missing evidence items.")
        critical_or_high = [
            f for f in findings if f.severity in ("critical", "high")
        ]
        if critical_or_high:
            recommendations.append(
                f"Prioritise review of {len(critical_or_high)} critical/high "
                "severity finding(s)."
            )

        summary_parts = [
            f"{len(result.collected)} evidence records collected",
            f"{len(result.missing)} missing",
            f"{len(findings)} finding(s) triggered",
        ]
        if findings:
            worst = min(
                findings,
                key=lambda f: ["critical", "high", "medium", "low", "informational"].index(
                    f.severity
                ),
            )
            summary_parts.append(f"worst severity: {worst.severity}")

        report = InvestigationReport(
            investigation_id=investigation_id,
            report_version=version,
            summary=". ".join(summary_parts) + ".",
            observed_facts=observed_facts,
            findings=findings_snapshot,
            missing_evidence=missing_evidence,
            recommendations=recommendations,
            limitations=limitations,
        )
        await report_repository.insert(session, report)
