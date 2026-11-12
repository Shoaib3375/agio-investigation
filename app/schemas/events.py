"""Event envelope schema matching Laravel's transactional outbox.

Verified in docs/AGIO_INVESTIGATION_READINESS_AUDIT.md (Section B):

* ``event_version`` is the STRING ``"1.0"`` (not the integer ``1``).
* ``entity`` is nested ``{type, id}`` (no flat entity_type/entity_id).
* ``causation_id`` is always ``null`` in the current Laravel factory.
* ``occurred_at`` is an ISO-8601 Zulu timestamp.
"""

from datetime import datetime

from pydantic import BaseModel, field_validator


class Entity(BaseModel):
    """Nested entity reference ``{type, id}``."""

    type: str
    id: str


class EventEnvelope(BaseModel):
    """The envelope published by Laravel's OutboxEventFactory::envelope()."""

    event_id: str
    event_type: str
    event_version: str = "1.0"
    occurred_at: datetime
    producer: str
    correlation_id: str
    causation_id: str | None = None
    entity: Entity
    data: dict

    @field_validator("event_version")
    @classmethod
    def _event_version_must_be_1_0(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError(f"Unsupported event_version: {value!r}; only '1.0' is supported")
        return value


# The 10 event types actually wired by Laravel's outbox (audit Section C).
# ``order.status_changed`` is the umbrella order-lifecycle event: classify the
# transition by ``data.to_status`` (completed, failed, refunded,
# deposit_detected, deposit_confirmed, payout_verified, amount_mismatch, ...).
# Do NOT expect separate order.completed/failed/refunded or deposit.* /
# payout.verified events — they are never emitted.
WIRED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "order.created",
        "order.status_changed",
        "risk_case.opened",
        "user.login.succeeded",
        "user.login.failed",
        "withdrawal.submitted",
        "withdrawal.approved",
        "withdrawal.rejected",
        "kyc.approved",
        "kyc.rejected",
    }
)
