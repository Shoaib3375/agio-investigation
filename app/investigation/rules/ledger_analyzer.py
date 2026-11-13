"""Ledger journal entry and posting rules.

Evaluates ledger evidence to detect:
- Missing journal entries for a completed order.
- Reversed journal entries.
- Journals still in pending state.
- Double-entry imbalance (sum of debits ≠ sum of credits).

All financial comparisons use Decimal. Never float.
"""

from __future__ import annotations

from decimal import Decimal

from app.investigation.rules.base import (
    Category,
    RuleContext,
    RuleResult,
    Severity,
    to_decimal,
)

_AMOUNT_TOLERANCE = Decimal("0.00000001")


def analyze(ctx: RuleContext) -> list[RuleResult]:
    results: list[RuleResult] = []

    order_status = (ctx.order or {}).get("status", "")
    evidence_ids = ctx.evidence_id_list("ledger", "order")

    # ── Rule LD-001: Completed order has no journal entries ────────────
    if order_status == "completed":
        if ctx.ledger is None:
            results.append(RuleResult(
                triggered=True,
                finding_code="LEDGER_EVIDENCE_MISSING",
                category=Category.LEDGER,
                severity=Severity.HIGH,
                title="Ledger evidence missing for completed order",
                description=(
                    f"Order '{ctx.external_reference}' is completed but ledger "
                    "evidence could not be retrieved."
                ),
                evidence_ids=ctx.evidence_id_list("order"),
                rule_version="1.0.0",
            ))
            return results

        entries: list[dict] = ctx.ledger.get("journal_entries", [])
        if not entries:
            results.append(RuleResult(
                triggered=True,
                finding_code="LEDGER_MISSING_JOURNAL",
                category=Category.LEDGER,
                severity=Severity.HIGH,
                title="Completed order has no ledger journal entries",
                description=(
                    f"Order '{ctx.external_reference}' is completed but no "
                    "journal entries exist. Expected at least one fee or "
                    "exchange journal."
                ),
                evidence_ids=evidence_ids,
                rule_version="1.0.0",
                confidence=Decimal("0.9"),
            ))
            return results

        for entry in entries:
            entry_id = entry.get("uuid", entry.get("id", "unknown"))

            # ── Rule LD-002: Reversed journal ─────────────────────────
            if entry.get("status") == "reversed":
                results.append(RuleResult(
                    triggered=True,
                    finding_code="LEDGER_JOURNAL_REVERSED",
                    category=Category.LEDGER,
                    severity=Severity.HIGH,
                    title="Journal entry has been reversed",
                    description=(
                        f"Journal entry '{entry_id}' for order "
                        f"'{ctx.external_reference}' has status 'reversed'. "
                        f"Description: {entry.get('description', 'N/A')}."
                    ),
                    evidence_ids=evidence_ids,
                    rule_version="1.0.0",
                    confidence=Decimal("0.95"),
                ))

            # ── Rule LD-003: Pending journal ──────────────────────────
            if entry.get("status") == "pending":
                results.append(RuleResult(
                    triggered=True,
                    finding_code="LEDGER_JOURNAL_PENDING",
                    category=Category.LEDGER,
                    severity=Severity.MEDIUM,
                    title="Journal entry is still pending",
                    description=(
                        f"Journal entry '{entry_id}' for order "
                        f"'{ctx.external_reference}' has status 'pending'. "
                        "Expected 'posted' for a completed order."
                    ),
                    evidence_ids=evidence_ids,
                    rule_version="1.0.0",
                    confidence=Decimal("0.9"),
                ))

            # ── Rule LD-004: Double-entry imbalance ───────────────────
            postings: list[dict] = entry.get("postings", [])
            if postings:
                total_debit = sum(to_decimal(p.get("debit")) for p in postings)
                total_credit = sum(to_decimal(p.get("credit")) for p in postings)
                imbalance = abs(total_debit - total_credit)
                if imbalance > _AMOUNT_TOLERANCE:
                    results.append(RuleResult(
                        triggered=True,
                        finding_code="LEDGER_POSTING_IMBALANCE",
                        category=Category.LEDGER,
                        severity=Severity.CRITICAL,
                        title="Journal entry double-entry is imbalanced",
                        description=(
                            f"Journal entry '{entry_id}': total debits "
                            f"({total_debit}) ≠ total credits ({total_credit}). "
                            f"Imbalance: {imbalance}. "
                            "This is a critical ledger integrity violation."
                        ),
                        evidence_ids=evidence_ids,
                        rule_version="1.0.0",
                        confidence=Decimal("0.99"),
                    ))

    return [r for r in results if r.triggered]
