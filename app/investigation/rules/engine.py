"""RuleEngine — orchestrates all analyzers against stored evidence.

Workflow:
  1. Load all EvidenceRecords for the investigation from the DB.
  2. Build a RuleContext from the snapshots.
  3. Run each analyzer (order, blockchain, provider, ledger, account).
  4. Persist triggered results as InvestigationFinding rows.
  5. Return a list of persisted findings.

The engine is deterministic and stateless — running it twice on the same
evidence produces identical findings (idempotent by finding_code per
investigation, duplicate codes are skipped on re-run).

Rule results are grounded in evidence — only evidence_ids from the
investigation's own EvidenceRecord rows are referenced in findings.
"""

from __future__ import annotations

from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    evidence_record_repository,
    finding_repository,
)
from app.investigation.rules import (
    account_analyzer,
    blockchain_analyzer,
    ledger_analyzer,
    order_analyzer,
    provider_analyzer,
)
from app.investigation.rules.base import RuleContext, RuleResult, RuleSource
from app.models.investigation_finding import InvestigationFinding

logger = structlog.get_logger()


class RuleEngine:
    """Runs all deterministic analyzers for one investigation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def analyze(
        self,
        investigation_id: str,
        external_reference: str,
        entity_type: str,
    ) -> list[InvestigationFinding]:
        """Load evidence, evaluate all rules, persist and return findings."""
        log = logger.bind(investigation_id=investigation_id)

        # ── 1. Load all evidence records ──────────────────────────────
        records = await evidence_record_repository.get_by_investigation(
            self._session, investigation_id
        )

        # ── 2. Build RuleContext from snapshots ───────────────────────
        ctx = RuleContext(
            investigation_id=investigation_id,
            external_reference=external_reference,
            entity_type=entity_type,
        )
        for rec in records:
            ctx.evidence_ids[rec.evidence_type] = rec.id
            snapshot = rec.snapshot or {}
            match rec.evidence_type:
                case "order":
                    ctx.order = snapshot
                case "timeline":
                    ctx.timeline = snapshot
                case "blockchain":
                    ctx.blockchain = snapshot
                case "provider":
                    ctx.provider = snapshot
                case "ledger":
                    ctx.ledger = snapshot
                case "reconciliation":
                    ctx.reconciliation = snapshot
                case "account":
                    ctx.account = snapshot
                case "risk":
                    ctx.risk = snapshot
                case "compliance":
                    ctx.compliance = snapshot
                case "wallet":
                    ctx.wallet = snapshot

        # ── 3. Run all analyzers ──────────────────────────────────────
        all_results: list[RuleResult] = []
        for analyzer_module, label in [
            (order_analyzer, "order"),
            (blockchain_analyzer, "blockchain"),
            (provider_analyzer, "provider"),
            (ledger_analyzer, "ledger"),
            (account_analyzer, "account"),
        ]:
            try:
                triggered = analyzer_module.analyze(ctx)
                all_results.extend(triggered)
                log.debug(
                    "rule_engine.analyzer_done",
                    analyzer=label,
                    triggered=len(triggered),
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "rule_engine.analyzer_error",
                    analyzer=label,
                    error=str(exc),
                )

        log.info(
            "rule_engine.analysis_complete",
            total_triggered=len(all_results),
        )

        if not all_results:
            return []

        # ── 4. Deduplicate: skip finding_codes already persisted ───────
        existing = await finding_repository.get_by_investigation(
            self._session, investigation_id
        )
        existing_codes = {f.finding_code for f in existing}

        # ── 5. Persist new findings ───────────────────────────────────
        new_findings: list[InvestigationFinding] = []
        for result in all_results:
            if result.finding_code in existing_codes:
                log.debug(
                    "rule_engine.finding_skipped_duplicate",
                    finding_code=result.finding_code,
                )
                continue

            finding = InvestigationFinding(
                investigation_id=investigation_id,
                finding_code=result.finding_code,
                category=result.category,
                severity=result.severity,
                confidence=result.confidence,
                title=result.title,
                description=result.description,
                evidence_ids=result.evidence_ids,
                source=RuleSource.DETERMINISTIC,
                rule_version=result.rule_version,
            )
            saved = await finding_repository.insert(self._session, finding)
            new_findings.append(saved)
            existing_codes.add(result.finding_code)

            log.info(
                "rule_engine.finding_persisted",
                finding_code=result.finding_code,
                severity=result.severity,
                category=result.category,
            )

        return new_findings
