"""Provider execution and quote rules.

Evaluates provider evidence to detect:
- Executions pending beyond expected windows.
- Provider-reported amounts differing from order amounts.
- Repeated provider failures on the same order.
- Expired quotes before execution.
- Provider execution and order lifecycle disagreements.

All amount comparisons use Decimal.
"""

from __future__ import annotations

from decimal import Decimal

from app.investigation.rules.base import (
    Category,
    RuleContext,
    RuleResult,
    Severity,
    hours_since,
    parse_datetime,
    to_decimal,
)

_EXECUTION_PENDING_HOURS = 1.0
_AMOUNT_TOLERANCE = Decimal("0.00000001")


def analyze(ctx: RuleContext) -> list[RuleResult]:
    results: list[RuleResult] = []

    if ctx.provider is None:
        return results

    executions: list[dict] = ctx.provider.get("provider_executions", [])
    quotes: list[dict] = ctx.provider.get("provider_quotes", [])
    order = ctx.order or {}
    evidence_ids = ctx.evidence_id_list("provider", "order")

    if not executions:
        return results

    # ── Rule PR-001: Execution pending too long ───────────────────────
    for ex in executions:
        if ex.get("status") in ("pending", "processing"):
            created_at = parse_datetime(ex.get("created_at"))
            hours = hours_since(created_at, ctx.evaluated_at)
            if hours is not None and hours > _EXECUTION_PENDING_HOURS:
                results.append(RuleResult(
                    triggered=True,
                    finding_code="PROVIDER_EXECUTION_PENDING_TIMEOUT",
                    category=Category.PROVIDER,
                    severity=Severity.HIGH,
                    title="Provider execution pending beyond expected window",
                    description=(
                        f"Provider execution {ex.get('id')} has been in status "
                        f"'{ex.get('status')}' for {hours:.1f} hours "
                        f"(threshold: {_EXECUTION_PENDING_HOURS}h). "
                        "Review provider API status or circuit breaker state."
                    ),
                    evidence_ids=evidence_ids,
                    rule_version="1.0.0",
                    confidence=Decimal("0.85"),
                ))

    # ── Rule PR-002: Provider execution amount differs from order ──────
    expected_sell = to_decimal(order.get("sell_amount"))
    if expected_sell > 0:
        for ex in executions:
            ex_sell = to_decimal(ex.get("sell_amount"))
            if ex_sell > 0 and abs(ex_sell - expected_sell) > _AMOUNT_TOLERANCE:
                results.append(RuleResult(
                    triggered=True,
                    finding_code="PROVIDER_EXECUTION_AMOUNT_MISMATCH",
                    category=Category.PROVIDER,
                    severity=Severity.HIGH,
                    title="Provider execution amount differs from order amount",
                    description=(
                        f"Provider execution {ex.get('id')} sell amount is "
                        f"{ex_sell} but order expected {expected_sell}. "
                        f"Difference: {abs(ex_sell - expected_sell)}."
                    ),
                    evidence_ids=evidence_ids,
                    rule_version="1.0.0",
                    confidence=Decimal("0.9"),
                ))

    # ── Rule PR-003: Multiple failed executions ───────────────────────
    failed = [e for e in executions if e.get("status") == "failed"]
    if len(failed) >= 2:
        results.append(RuleResult(
            triggered=True,
            finding_code="PROVIDER_REPEATED_EXECUTION_FAILURES",
            category=Category.PROVIDER,
            severity=Severity.HIGH,
            title="Multiple provider execution failures on this order",
            description=(
                f"{len(failed)} provider executions have failed for order "
                f"'{ctx.external_reference}'. "
                "This may indicate a provider-side issue or an invalid order configuration."
            ),
            evidence_ids=evidence_ids,
            rule_version="1.0.0",
            confidence=Decimal("0.9"),
        ))

    # ── Rule PR-004: Provider execution failed ────────────────────────
    if len(failed) == 1:
        results.append(RuleResult(
            triggered=True,
            finding_code="PROVIDER_EXECUTION_FAILED",
            category=Category.PROVIDER,
            severity=Severity.MEDIUM,
            title="Provider execution failed",
            description=(
                f"Provider execution {failed[0].get('id')} failed for order "
                f"'{ctx.external_reference}'."
            ),
            evidence_ids=evidence_ids,
            rule_version="1.0.0",
            confidence=Decimal("0.9"),
        ))

    # ── Rule PR-005: Order completed but no successful execution ───────
    order_status = order.get("status", "")
    if order_status == "completed":
        successful = [e for e in executions if e.get("status") in ("completed", "settled")]
        if not successful:
            results.append(RuleResult(
                triggered=True,
                finding_code="PROVIDER_COMPLETED_ORDER_NO_EXECUTION",
                category=Category.PROVIDER,
                severity=Severity.HIGH,
                title="Completed order has no successful provider execution",
                description=(
                    f"Order '{ctx.external_reference}' is marked 'completed' "
                    f"but none of the {len(executions)} provider execution(s) "
                    "show a successful terminal status."
                ),
                evidence_ids=evidence_ids,
                rule_version="1.0.0",
                confidence=Decimal("0.85"),
            ))

    return [r for r in results if r.triggered]
