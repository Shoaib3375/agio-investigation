"""InvestigationService — creates, correlates, and updates investigations.

Deduplication policy:
  When a new event arrives for an entity that already has an active
  (non-terminal) investigation, the event is correlated into the existing
  investigation rather than creating a new one. A new investigation is only
  created when no active one exists.

This service owns the investigation lifecycle state machine:
  queued → collecting → analyzing → completed | failed | review_required

It does NOT collect evidence directly — that is EvidenceCollector's job.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import investigation_repository
from app.models.event_inbox import EventInbox
from app.models.investigation import Investigation

logger = structlog.get_logger()

# Map event_type → investigation_type
_EVENT_TYPE_MAP: dict[str, str] = {
    "order.created":        "order_lifecycle",
    "order.status_changed": "order_lifecycle",
    "withdrawal.submitted": "order_lifecycle",
    "withdrawal.approved":  "order_lifecycle",
    "withdrawal.rejected":  "order_lifecycle",
    "risk_case.opened":     "account_risk",
    "user.login.succeeded": "account_risk",
    "user.login.failed":    "account_risk",
    "kyc.approved":         "account_risk",
    "kyc.rejected":         "account_risk",
}

# Map event_type → entity_type (what the investigation targets)
_ENTITY_TYPE_MAP: dict[str, str] = {
    "order.created":        "order",
    "order.status_changed": "order",
    "withdrawal.submitted": "withdrawal",
    "withdrawal.approved":  "withdrawal",
    "withdrawal.rejected":  "withdrawal",
    "risk_case.opened":     "risk_case",
    "user.login.succeeded": "user",
    "user.login.failed":    "user",
    "kyc.approved":         "user",
    "kyc.rejected":         "user",
}

# Statuses that warrant immediate investigation (not every event does)
_TRIGGERING_STATUSES = {
    "deposit_detected",
    "deposit_confirmed",
    "amount_mismatch",
    "reconciliation_required",
    "payout_verified",
    "provider_hold",
    "failed",
    "refund_pending",
    "refunded",
}


class InvestigationService:
    """Manages investigation lifecycle for a single DB session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_for_event(
        self, event: EventInbox
    ) -> tuple[Investigation, bool]:
        """Return (investigation, created) for the given inbox event.

        - If an active investigation exists for the same entity, return it
          (created=False) — event is correlated in.
        - Otherwise create a new one (created=True).

        Returns (None, False) if the event does not trigger an investigation
        (e.g. a login success with no anomaly context).
        """
        entity_type = _ENTITY_TYPE_MAP.get(event.event_type)
        investigation_type = _EVENT_TYPE_MAP.get(event.event_type, "order_lifecycle")

        if not entity_type:
            logger.debug(
                "investigation_service.no_entity_type",
                event_id=event.event_id,
                event_type=event.event_type,
            )
            return None, False  # type: ignore[return-value]

        # For order.status_changed only create/update when it's a significant transition
        if event.event_type == "order.status_changed":
            to_status = (event.payload or {}).get("data", {}).get("current_status", "")
            if to_status and to_status not in _TRIGGERING_STATUSES:
                logger.debug(
                    "investigation_service.status_not_triggering",
                    event_id=event.event_id,
                    to_status=to_status,
                )
                return None, False  # type: ignore[return-value]

        external_reference = event.entity_id or ""
        if not external_reference:
            logger.warning(
                "investigation_service.missing_entity_id",
                event_id=event.event_id,
                event_type=event.event_type,
            )
            return None, False  # type: ignore[return-value]

        # Try to find an existing active investigation for this entity
        existing = await investigation_repository.get_active_for_entity(
            self._session,
            entity_type=entity_type,
            external_reference=external_reference,
        )
        if existing:
            logger.info(
                "investigation_service.correlated",
                investigation_id=existing.id,
                event_id=event.event_id,
                entity_type=entity_type,
                external_reference=external_reference,
            )
            return existing, False

        # Create new
        investigation = await investigation_repository.create(
            self._session,
            external_reference=external_reference,
            entity_type=entity_type,
            trigger_event_id=event.event_id,
            correlation_id=event.correlation_id,
            investigation_type=investigation_type,
            priority=self._derive_priority(event),
        )
        return investigation, True

    async def transition(
        self,
        investigation_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        """Move an investigation to a new lifecycle status."""
        await investigation_repository.set_status(
            self._session,
            investigation_id=investigation_id,
            status=status,
            error=error,
        )
        logger.info(
            "investigation_service.transition",
            investigation_id=investigation_id,
            status=status,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_priority(event: EventInbox) -> str:
        """Derive initial priority from event type and payload."""
        if event.event_type == "risk_case.opened":
            return "high"
        data = (event.payload or {}).get("data", {})
        to_status = data.get("current_status", "")
        if to_status in {"amount_mismatch", "reconciliation_required", "failed"}:
            return "high"
        if to_status in {"deposit_detected", "deposit_confirmed", "payout_verified"}:
            return "normal"
        return "normal"
