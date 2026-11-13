"""Order lifecycle rules.

Rules evaluate the order snapshot and timeline evidence to detect:
- Deposits not arriving within expected windows.
- Unexpected or inconsistent status transitions.
- Orders stuck in intermediate states too long.
- Completed orders with missing expected evidence.
"""

from __future__ import annotations

from decimal import Decimal

from app.investigation.rules.base import (
    Category,
    RuleContext,
    RuleResult,
    RuleSource,
    Severity,
    hours_since,
    parse_datetime,
    to_decimal,
)

# Configurable thresholds (hours)
_AWAITING_DEPOSIT_HOURS = 2.0
_DEPOSIT_DETECTED_UNCONFIRMED_HOURS = 4.0
_PROVIDER_EXECUTION_PENDING_HOURS = 1.0
_STUCK_INTERMEDIATE_HOURS = 6.0

# Statuses that should NOT persist beyond their expected window
_INTERMEDIATE_STATUSES = {
    "deposit_detected",
    "risk_review",
    "provider_execution_pending",
    "settlement_pending",
    "refund_pending",
    "payout_pending",
}

# Terminal statuses (not stuck)
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "refunded"}


def analyze(ctx: RuleContext) -> list[RuleResult]:
    """Evaluate all order rules and return triggered results."""
    results: list[RuleResult] = []

    if ctx.order is None:
        results.append(RuleResult(
            triggered=True,
            finding_code="ORDER_EVIDENCE_MISSING",
            category=Category.ORDER,
            severity=Severity.HIGH,
            title="Order evidence could not be retrieved",
            description=(
                f"No order record was retrieved for reference "
                f"'{ctx.external_reference}'. "
                "Investigation cannot proceed without authoritative order data."
            ),
            evidence_ids=[],
            rule_version="1.0.0",
        ))
        return results

    order = ctx.order
    status = order.get("status", "")
    created_at = parse_datetime(order.get("created_at"))
    evidence_ids = ctx.evidence_id_list("order", "timeline")

    # ── Rule ORD-001: Order stuck awaiting deposit ─────────────────────
    if status == "awaiting_deposit":
        hours = hours_since(created_at, ctx.evaluated_at)
        if hours is not None and hours > _AWAITING_DEPOSIT_HOURS:
            results.append(RuleResult(
                triggered=True,
                finding_code="ORDER_AWAITING_DEPOSIT_TIMEOUT",
                category=Category.ORDER,
                severity=Severity.MEDIUM,
                title="Order awaiting deposit beyond expected window",
                description=(
                    f"Order '{ctx.external_reference}' has been in 'awaiting_deposit' "
                    f"for {hours:.1f} hours (threshold: {_AWAITING_DEPOSIT_HOURS}h). "
                    "The customer may have abandoned the exchange or encountered a "
                    "deposit issue."
                ),
                evidence_ids=evidence_ids,
                rule_version="1.0.0",
                confidence=Decimal("0.8"),
            ))

    # ── Rule ORD-002: Deposit detected but not confirmed ──────────────
    if status == "deposit_detected":
        deposit_detected_at = parse_datetime(order.get("deposit_detected_at"))
        hours = hours_since(deposit_detected_at, ctx.evaluated_at)
        if hours is not None and hours > _DEPOSIT_DETECTED_UNCONFIRMED_HOURS:
            results.append(RuleResult(
                triggered=True,
                finding_code="ORDER_DEPOSIT_UNCONFIRMED_TIMEOUT",
                category=Category.ORDER,
                severity=Severity.MEDIUM,
                title="Deposit detected but not confirmed within expected window",
                description=(
                    f"Order '{ctx.external_reference}' deposit was detected "
                    f"{hours:.1f} hours ago but remains unconfirmed "
                    f"(threshold: {_DEPOSIT_DETECTED_UNCONFIRMED_HOURS}h). "
                    "Review blockchain confirmation progress."
                ),
                evidence_ids=ctx.evidence_id_list("order", "blockchain"),
                rule_version="1.0.0",
                confidence=Decimal("0.85"),
            ))

    # ── Rule ORD-003: Amount mismatch status ───────────────────────────
    if status == "amount_mismatch":
        results.append(RuleResult(
            triggered=True,
            finding_code="ORDER_AMOUNT_MISMATCH",
            category=Category.FINANCIAL,
            severity=Severity.HIGH,
            title="Order is in amount_mismatch status",
            description=(
                f"Order '{ctx.external_reference}' has status 'amount_mismatch'. "
                "The received deposit amount differs from the expected amount. "
                "Review blockchain deposit evidence for the discrepancy."
            ),
            evidence_ids=ctx.evidence_id_list("order", "blockchain"),
            rule_version="1.0.0",
            confidence=Decimal("0.95"),
        ))

    # ── Rule ORD-004: Reconciliation required ─────────────────────────
    if status == "reconciliation_required":
        results.append(RuleResult(
            triggered=True,
            finding_code="ORDER_RECONCILIATION_REQUIRED",
            category=Category.RECONCILIATION,
            severity=Severity.HIGH,
            title="Order requires reconciliation",
            description=(
                f"Order '{ctx.external_reference}' has status "
                "'reconciliation_required'. "
                "A provider, deposit, or payout inconsistency has been flagged "
                "by the exchange system."
            ),
            evidence_ids=ctx.evidence_id_list("order", "reconciliation"),
            rule_version="1.0.0",
            confidence=Decimal("0.95"),
        ))

    # ── Rule ORD-005: Order stuck in intermediate status ───────────────
    if status in _INTERMEDIATE_STATUSES:
        entered_at = _get_status_entry_time(ctx.timeline, status)
        if entered_at is None:
            entered_at = created_at
        hours = hours_since(entered_at, ctx.evaluated_at)
        if hours is not None and hours > _STUCK_INTERMEDIATE_HOURS:
            results.append(RuleResult(
                triggered=True,
                finding_code="ORDER_STUCK_INTERMEDIATE_STATUS",
                category=Category.ORDER,
                severity=Severity.MEDIUM,
                title=f"Order stuck in '{status}' beyond expected window",
                description=(
                    f"Order '{ctx.external_reference}' has been in status "
                    f"'{status}' for {hours:.1f} hours "
                    f"(threshold: {_STUCK_INTERMEDIATE_HOURS}h). "
                    "Manual review may be required."
                ),
                evidence_ids=ctx.evidence_id_list("order", "timeline"),
                rule_version="1.0.0",
                confidence=Decimal("0.75"),
            ))

    # ── Rule ORD-006: Completed order missing blockchain evidence ──────
    if status == "completed" and ctx.blockchain is not None:
        deposits = ctx.blockchain.get("blockchain_deposits", [])
        payouts = ctx.blockchain.get("blockchain_payouts", [])
        if not deposits:
            results.append(RuleResult(
                triggered=True,
                finding_code="ORDER_COMPLETED_MISSING_DEPOSIT_EVIDENCE",
                category=Category.BLOCKCHAIN,
                severity=Severity.MEDIUM,
                title="Completed order has no blockchain deposit evidence",
                description=(
                    f"Order '{ctx.external_reference}' is completed but no "
                    "blockchain deposit records exist. "
                    "Expected at least one confirmed deposit."
                ),
                evidence_ids=ctx.evidence_id_list("order", "blockchain"),
                rule_version="1.0.0",
            ))
        if not payouts:
            results.append(RuleResult(
                triggered=True,
                finding_code="ORDER_COMPLETED_MISSING_PAYOUT_EVIDENCE",
                category=Category.BLOCKCHAIN,
                severity=Severity.MEDIUM,
                title="Completed order has no blockchain payout evidence",
                description=(
                    f"Order '{ctx.external_reference}' is completed but no "
                    "blockchain payout records exist."
                ),
                evidence_ids=ctx.evidence_id_list("order", "blockchain"),
                rule_version="1.0.0",
            ))

    # ── Rule ORD-007: Failed order with no timeline events ─────────────
    if status == "failed" and ctx.timeline is not None:
        events = ctx.timeline.get("order_events", [])
        if not events:
            results.append(RuleResult(
                triggered=True,
                finding_code="ORDER_FAILED_NO_TIMELINE",
                category=Category.ORDER,
                severity=Severity.LOW,
                title="Failed order has no recorded lifecycle events",
                description=(
                    f"Order '{ctx.external_reference}' has status 'failed' but "
                    "the order_events table contains no records. "
                    "This may indicate a data integrity issue."
                ),
                evidence_ids=ctx.evidence_id_list("order", "timeline"),
                rule_version="1.0.0",
            ))

    return [r for r in results if r.triggered]


def _get_status_entry_time(timeline: dict | None, status: str):
    """Find the created_at of the first event that transitioned TO status."""
    if timeline is None:
        return None
    for event in timeline.get("order_events", []):
        if event.get("to_status") == status:
            return parse_datetime(event.get("created_at"))
    return None
