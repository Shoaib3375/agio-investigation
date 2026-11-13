"""EvidenceCollector — retrieves evidence from Laravel and persists it.

Each piece of evidence is stored as an EvidenceRecord with:
  - Provenance: source system, record type, record ID, retrieval timestamp.
  - Integrity: SHA-256 hex digest of the canonicalised JSON snapshot.
  - Sanitised snapshot: no secrets, no raw provider credentials.

Evidence that cannot be retrieved (404 / timeout / unavailable) is recorded
as a MissingEvidence entry in the collector result, not silently dropped.

The collector is read-only with respect to Laravel — it never modifies
financial state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import evidence_record_repository
from app.integrations.laravel.client import LaravelApiError, LaravelNotFoundError
from app.integrations.laravel.evidence_client import EvidenceClient
from app.models.evidence_record import EvidenceRecord

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CollectedEvidence:
    """A successfully retrieved and stored evidence record."""
    record_id: str          # UUID of the EvidenceRecord row
    evidence_type: str
    source_record_type: str
    source_record_id: str


@dataclass
class MissingEvidence:
    """Evidence that could not be retrieved — recorded explicitly."""
    evidence_type: str
    source_record_type: str
    source_record_id: str
    reason: str             # "not_found" | "api_error" | "timeout" | "unavailable"
    detail: str             # sanitised error message (no secrets)


@dataclass
class CollectionResult:
    """Full result of one evidence collection run."""
    investigation_id: str
    collected: list[CollectedEvidence] = field(default_factory=list)
    missing: list[MissingEvidence] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return len(self.missing) == 0

    def summary(self) -> dict[str, Any]:
        return {
            "investigation_id": self.investigation_id,
            "collected_count": len(self.collected),
            "missing_count": len(self.missing),
            "is_complete": self.is_complete,
            "missing": [
                {"evidence_type": m.evidence_type, "reason": m.reason}
                for m in self.missing
            ],
        }


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------


def _content_hash(data: Any) -> str:
    """Return SHA-256 hex digest of the canonicalised JSON representation."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# EvidenceCollector
# ---------------------------------------------------------------------------


class EvidenceCollector:
    """Collects and persists evidence for one investigation.

    Usage::

        async with LaravelClient.from_settings(settings) as http:
            ec = EvidenceClient(http)
            collector = EvidenceCollector(ec, session)
            result = await collector.collect_for_order(
                investigation_id="...",
                order_uuid="...",
                user_id=6,
            )
    """

    def __init__(self, evidence_client: EvidenceClient, session: AsyncSession) -> None:
        self._client = evidence_client
        self._session = session

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def collect_for_order(
        self,
        investigation_id: str,
        order_uuid: str,
        user_id: int | None = None,
    ) -> CollectionResult:
        """Full evidence sweep for an order-based investigation.

        Collection order per spec section 9.2:
          1. Order details
          2. Order timeline
          3. Blockchain deposits + payouts
          4. Provider executions + quotes
          5. Ledger journal entries
          6. Reconciliation cases + recovery actions
          7. Account + risk context (when user_id provided)
        """
        result = CollectionResult(investigation_id=investigation_id)
        log = logger.bind(investigation_id=investigation_id, order_uuid=order_uuid)
        log.info("evidence_collector.order.start")

        # 1. Order details
        await self._collect(
            result,
            evidence_type="order",
            source_record_type="order",
            source_record_id=order_uuid,
            fetch=lambda: self._client.order(order_uuid),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )

        # 2. Order timeline
        await self._collect(
            result,
            evidence_type="timeline",
            source_record_type="order_events",
            source_record_id=order_uuid,
            fetch=lambda: self._client.order_timeline(order_uuid),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )

        # 3. Blockchain
        await self._collect(
            result,
            evidence_type="blockchain",
            source_record_type="blockchain_deposits_payouts",
            source_record_id=order_uuid,
            fetch=lambda: self._client.order_blockchain(order_uuid),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )

        # 4. Provider
        await self._collect(
            result,
            evidence_type="provider",
            source_record_type="provider_executions_quotes",
            source_record_id=order_uuid,
            fetch=lambda: self._client.order_provider(order_uuid),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )

        # 5. Ledger
        await self._collect(
            result,
            evidence_type="ledger",
            source_record_type="journal_entries",
            source_record_id=order_uuid,
            fetch=lambda: self._client.order_ledger(order_uuid),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )

        # 6. Reconciliation
        await self._collect(
            result,
            evidence_type="reconciliation",
            source_record_type="reconciliation_cases",
            source_record_id=order_uuid,
            fetch=lambda: self._client.order_reconciliation(order_uuid),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )

        # 7. Account context (optional)
        if user_id is not None:
            await self._collect_account(result, user_id)

        order_summary = result.summary()
        order_summary.pop("investigation_id", None)
        log.info("evidence_collector.order.done", **order_summary)
        return result

    async def collect_for_user(
        self,
        investigation_id: str,
        user_id: int,
    ) -> CollectionResult:
        """Account-focused evidence sweep (risk cases, compliance, activity)."""
        result = CollectionResult(investigation_id=investigation_id)
        await self._collect_account(result, user_id)
        summary = result.summary()
        summary.pop("investigation_id", None)
        logger.info(
            "evidence_collector.user.done",
            investigation_id=investigation_id,
            **summary,
        )
        return result

    # ------------------------------------------------------------------
    # Account collection (shared by both entry points)
    # ------------------------------------------------------------------

    async def _collect_account(self, result: CollectionResult, user_id: int) -> None:
        uid = str(user_id)

        await self._collect(
            result,
            evidence_type="account",
            source_record_type="user_profile",
            source_record_id=uid,
            fetch=lambda: self._client.user_profile(user_id),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )
        await self._collect(
            result,
            evidence_type="risk",
            source_record_type="user_risk_summary",
            source_record_id=uid,
            fetch=lambda: self._client.user_risk_summary(user_id),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )
        await self._collect(
            result,
            evidence_type="compliance",
            source_record_type="user_compliance",
            source_record_id=uid,
            fetch=lambda: self._client.user_compliance(user_id),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )
        await self._collect(
            result,
            evidence_type="wallet",
            source_record_type="user_wallets",
            source_record_id=uid,
            fetch=lambda: self._client.user_wallets(user_id),
            serialise=lambda r: r.model_dump(mode="json"),
            verification_method="schema_match",
        )

    # ------------------------------------------------------------------
    # Core collection helper
    # ------------------------------------------------------------------

    async def _collect(
        self,
        result: CollectionResult,
        *,
        evidence_type: str,
        source_record_type: str,
        source_record_id: str,
        fetch,       # async callable → response model | None
        serialise,   # callable(response) → dict
        verification_method: str = "schema_match",
    ) -> None:
        """Fetch, hash, persist — or record missing evidence on failure."""
        investigation_id = result.investigation_id
        retrieved_at = datetime.now(tz=UTC)

        try:
            response = await fetch()
        except LaravelNotFoundError:
            result.missing.append(MissingEvidence(
                evidence_type=evidence_type,
                source_record_type=source_record_type,
                source_record_id=source_record_id,
                reason="not_found",
                detail=f"{source_record_type}:{source_record_id} returned 404",
            ))
            return
        except LaravelApiError as exc:
            reason = type(exc).__name__.replace("Laravel", "").replace("Error", "").lower()
            result.missing.append(MissingEvidence(
                evidence_type=evidence_type,
                source_record_type=source_record_type,
                source_record_id=source_record_id,
                reason=reason or "api_error",
                detail=str(exc)[:500],
            ))
            logger.warning(
                "evidence_collector.api_error",
                investigation_id=investigation_id,
                evidence_type=evidence_type,
                error=str(exc),
            )
            return

        if response is None:
            # EvidenceClient returns None on 404
            result.missing.append(MissingEvidence(
                evidence_type=evidence_type,
                source_record_type=source_record_type,
                source_record_id=source_record_id,
                reason="not_found",
                detail=f"{source_record_type}:{source_record_id} not found",
            ))
            return

        snapshot = serialise(response)
        content_hash = _content_hash(snapshot)

        record = EvidenceRecord(
            investigation_id=investigation_id,
            evidence_type=evidence_type,
            source_system="agio-paymentexchange",
            source_record_type=source_record_type,
            source_record_id=source_record_id,
            retrieved_at=retrieved_at,
            content_hash=content_hash,
            is_verified=True,
            verification_method=verification_method,
            snapshot=snapshot,
        )
        saved = await evidence_record_repository.insert(self._session, record)

        result.collected.append(CollectedEvidence(
            record_id=saved.id,
            evidence_type=evidence_type,
            source_record_type=source_record_type,
            source_record_id=source_record_id,
        ))
        logger.debug(
            "evidence_collector.stored",
            investigation_id=investigation_id,
            evidence_type=evidence_type,
            source_record_id=source_record_id,
            content_hash=content_hash[:12],
        )
