"""Blockchain deposit and payout rules.

Evaluates blockchain evidence to detect:
- Deposit amount mismatches (underpaid / overpaid).
- Confirmation delays.
- Duplicate transaction hashes.
- Payout address mismatches.
- Missing or incomplete blockchain evidence.

Network-specific semantics (BTC, ETH/ERC20, TRON/TRC20, BSC, Polygon, Solana)
differ in confirmation requirements. The rules use the stored
required_confirmations field from the deposit record rather than hardcoding
per-network values.

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

# Tolerance for "exact match" comparisons (avoid false positives from rounding)
_AMOUNT_TOLERANCE = Decimal("0.00000001")
_CONFIRMATION_DELAY_HOURS = 2.0


def analyze(ctx: RuleContext) -> list[RuleResult]:
    results: list[RuleResult] = []

    if ctx.blockchain is None:
        # Missing blockchain evidence is recorded by EvidenceCollector;
        # the order analyzer will note missing deposit evidence if needed.
        return results

    deposits: list[dict] = ctx.blockchain.get("blockchain_deposits", [])
    payouts: list[dict] = ctx.blockchain.get("blockchain_payouts", [])
    evidence_ids = ctx.evidence_id_list("blockchain", "order")

    # ── Rule BC-001: Deposit amount mismatch ──────────────────────────
    for dep in deposits:
        received = to_decimal(dep.get("received_amount"))
        expected = to_decimal(dep.get("expected_amount"))
        difference = abs(received - expected)
        status = dep.get("status", "")

        if difference > _AMOUNT_TOLERANCE:
            if received < expected:
                severity = Severity.HIGH
                code = "BLOCKCHAIN_DEPOSIT_UNDERPAID"
                title = "Blockchain deposit is underpaid"
                desc = (
                    f"Deposit {dep.get('id')} received {received} "
                    f"{dep.get('currency_code', '')} but expected {expected}. "
                    f"Shortfall: {expected - received}. "
                    f"Deposit status: {status}."
                )
            else:
                severity = Severity.MEDIUM
                code = "BLOCKCHAIN_DEPOSIT_OVERPAID"
                title = "Blockchain deposit is overpaid"
                desc = (
                    f"Deposit {dep.get('id')} received {received} "
                    f"{dep.get('currency_code', '')} but expected {expected}. "
                    f"Surplus: {received - expected}. "
                    f"Deposit status: {status}."
                )
            results.append(RuleResult(
                triggered=True,
                finding_code=code,
                category=Category.BLOCKCHAIN,
                severity=severity,
                title=title,
                description=desc,
                evidence_ids=evidence_ids,
                rule_version="1.0.0",
                confidence=Decimal("0.95"),
            ))

    # ── Rule BC-002: Confirmation delay ───────────────────────────────
    for dep in deposits:
        if dep.get("status") in ("detected", "confirming"):
            detected_at = parse_datetime(dep.get("detected_at"))
            hours = hours_since(detected_at, ctx.evaluated_at)
            confirmations = dep.get("confirmations", 0)
            required = dep.get("required_confirmations", 1)
            if hours is not None and hours > _CONFIRMATION_DELAY_HOURS and confirmations < required:
                results.append(RuleResult(
                    triggered=True,
                    finding_code="BLOCKCHAIN_CONFIRMATION_DELAY",
                    category=Category.BLOCKCHAIN,
                    severity=Severity.MEDIUM,
                    title="Blockchain deposit confirmation is delayed",
                    description=(
                        f"Deposit {dep.get('id')} on network "
                        f"'{dep.get('network_identifier', 'unknown')}' "
                        f"has {confirmations}/{required} confirmations "
                        f"after {hours:.1f} hours. "
                        "This may indicate network congestion or an orphaned transaction."
                    ),
                    evidence_ids=evidence_ids,
                    rule_version="1.0.0",
                    confidence=Decimal("0.8"),
                ))

    # ── Rule BC-003: Duplicate transaction hash across deposits ────────
    tx_hashes = [d.get("tx_hash") for d in deposits if d.get("tx_hash")]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for h in tx_hashes:
        if h in seen:
            duplicates.add(h)
        seen.add(h)
    if duplicates:
        results.append(RuleResult(
            triggered=True,
            finding_code="BLOCKCHAIN_DUPLICATE_TX_HASH",
            category=Category.BLOCKCHAIN,
            severity=Severity.HIGH,
            title="Duplicate transaction hash in blockchain deposits",
            description=(
                f"Transaction hash(es) {list(duplicates)} appear more than once "
                "in the deposit records for this order. "
                "This may indicate double-processing or a data integrity issue."
            ),
            evidence_ids=evidence_ids,
            rule_version="1.0.0",
            confidence=Decimal("0.9"),
        ))

    # ── Rule BC-004: Payout address differs from order destination ─────
    order_destination = (ctx.order or {}).get("destination_address")
    if order_destination:
        for payout in payouts:
            payout_address = payout.get("payout_address")
            if payout_address and payout_address != order_destination:
                results.append(RuleResult(
                    triggered=True,
                    finding_code="BLOCKCHAIN_PAYOUT_ADDRESS_MISMATCH",
                    category=Category.BLOCKCHAIN,
                    severity=Severity.CRITICAL,
                    title="Payout address differs from order destination address",
                    description=(
                        f"Payout {payout.get('id')} sent to address "
                        f"'{payout_address}' but the order destination is "
                        f"'{order_destination}'. "
                        "This requires immediate manual review."
                    ),
                    evidence_ids=ctx.evidence_id_list("blockchain", "order"),
                    rule_version="1.0.0",
                    confidence=Decimal("0.99"),
                ))

    # ── Rule BC-005: Completed order with no confirmed deposit ─────────
    order_status = (ctx.order or {}).get("status", "")
    if order_status == "completed" and deposits:
        confirmed = [d for d in deposits if d.get("status") == "confirmed"]
        if not confirmed:
            results.append(RuleResult(
                triggered=True,
                finding_code="BLOCKCHAIN_NO_CONFIRMED_DEPOSIT",
                category=Category.BLOCKCHAIN,
                severity=Severity.HIGH,
                title="Order completed with no confirmed blockchain deposit",
                description=(
                    f"Order '{ctx.external_reference}' is marked 'completed' "
                    f"but none of the {len(deposits)} blockchain deposit(s) "
                    "have status 'confirmed'. "
                    "Review deposit and confirmation records."
                ),
                evidence_ids=evidence_ids,
                rule_version="1.0.0",
                confidence=Decimal("0.9"),
            ))

    return [r for r in results if r.triggered]
