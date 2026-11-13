"""Account and risk rules.

Evaluates user account, risk, and compliance evidence to detect:
- Accounts with repeated open risk cases.
- KYC status incompatible with exchange operations.
- Screening results that are stubs (zero score, 'internal' provider).
- Compliance evidence unavailability.

These rules surface information — they do not make AML determinations.
A finding from this analyzer is never a final compliance decision.
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

_OPEN_RISK_CASE_THRESHOLD = 2
_KYC_REQUIRED_STATUSES = {"approved"}
_STUB_SCREENING_PROVIDERS = {"internal", "stub", "mock"}


def analyze(ctx: RuleContext) -> list[RuleResult]:
    results: list[RuleResult] = []

    # Only run account rules when account evidence is available
    if ctx.account is None and ctx.risk is None and ctx.compliance is None:
        return results

    account = ctx.account or {}
    risk_data = ctx.risk or {}
    compliance = ctx.compliance or {}
    evidence_ids = ctx.evidence_id_list("account", "risk", "compliance")

    # ── Rule AC-001: Multiple open risk cases ──────────────────────────
    risk_cases = risk_data.get("risk_cases", [])
    open_cases = [c for c in risk_cases if c.get("status") in
                  ("open", "investigating", "escalated")]
    if len(open_cases) >= _OPEN_RISK_CASE_THRESHOLD:
        severities = {c.get("severity", "medium") for c in open_cases}
        worst = _worst_severity(severities)
        results.append(RuleResult(
            triggered=True,
            finding_code="ACCOUNT_MULTIPLE_OPEN_RISK_CASES",
            category=Category.ACCOUNT,
            severity=worst,
            title=f"Account has {len(open_cases)} open risk cases",
            description=(
                f"User account has {len(open_cases)} open risk case(s) "
                f"(severities: {', '.join(severities)}). "
                "Review each case before processing further transactions."
            ),
            evidence_ids=evidence_ids,
            rule_version="1.0.0",
            confidence=Decimal("0.9"),
        ))

    # ── Rule AC-002: KYC not approved ─────────────────────────────────
    kyc_status = account.get("kyc_status", "")
    account_status = account.get("status", "active")
    if kyc_status and kyc_status not in _KYC_REQUIRED_STATUSES and account_status == "active":
        results.append(RuleResult(
            triggered=True,
            finding_code="ACCOUNT_KYC_NOT_APPROVED",
            category=Category.COMPLIANCE,
            severity=Severity.MEDIUM,
            title=f"Account KYC status is '{kyc_status}'",
            description=(
                f"The account's KYC status is '{kyc_status}'. "
                "Exchange transactions typically require approved KYC. "
                "Verify whether this transaction is permitted under current "
                "KYC status."
            ),
            evidence_ids=evidence_ids,
            rule_version="1.0.0",
            confidence=Decimal("0.8"),
        ))

    # ── Rule AC-003: Account inactive / suspended ──────────────────────
    if account_status in ("inactive", "suspended", "banned"):
        results.append(RuleResult(
            triggered=True,
            finding_code="ACCOUNT_STATUS_RESTRICTED",
            category=Category.ACCOUNT,
            severity=Severity.HIGH,
            title=f"Account status is '{account_status}'",
            description=(
                f"The account involved in this investigation has status "
                f"'{account_status}'. "
                "Review whether transactions by this account are permitted."
            ),
            evidence_ids=evidence_ids,
            rule_version="1.0.0",
            confidence=Decimal("0.95"),
        ))

    # ── Rule AC-004: Screening stub detection ─────────────────────────
    # A stub screening returns "clear" with risk_score=0 from an internal provider.
    # This is NOT genuine AML clearance — flag it so reviewers know.
    screening_results = (
        risk_data.get("screening_results", [])
        + compliance.get("screening_results", [])
    )
    for sr in screening_results:
        provider = str(sr.get("provider", "")).lower()
        risk_score = to_decimal(sr.get("risk_score"), Decimal("0"))
        result_val = sr.get("result", "")
        if provider in _STUB_SCREENING_PROVIDERS and risk_score == Decimal("0") and result_val == "clear":
            results.append(RuleResult(
                triggered=True,
                finding_code="ACCOUNT_STUB_SCREENING_RESULT",
                category=Category.COMPLIANCE,
                severity=Severity.INFORMATIONAL,
                title="Screening result appears to be a stub (zero risk score)",
                description=(
                    f"Screening result from provider '{provider}' shows "
                    f"result='{result_val}' with risk_score=0. "
                    "This may be a placeholder or stub screening. "
                    "It does not constitute genuine AML clearance."
                ),
                evidence_ids=evidence_ids,
                rule_version="1.0.0",
                confidence=Decimal("0.7"),
            ))
            break  # one finding per investigation is enough

    # ── Rule AC-005: Flagged orders ───────────────────────────────────
    flagged_orders = risk_data.get("flagged_orders", [])
    high_risk_flags = [
        o for o in flagged_orders
        if o.get("risk_status") in ("flagged", "rejected")
    ]
    if high_risk_flags:
        results.append(RuleResult(
            triggered=True,
            finding_code="ACCOUNT_HAS_FLAGGED_ORDERS",
            category=Category.ACCOUNT,
            severity=Severity.MEDIUM,
            title=f"Account has {len(high_risk_flags)} flagged or rejected order(s)",
            description=(
                f"The account has {len(high_risk_flags)} order(s) with "
                "risk_status 'flagged' or 'rejected'. "
                "Review historical context before concluding on this investigation."
            ),
            evidence_ids=evidence_ids,
            rule_version="1.0.0",
            confidence=Decimal("0.8"),
        ))

    return [r for r in results if r.triggered]


def _worst_severity(severities: set[str]) -> str:
    """Return the highest severity from a set of severity strings."""
    _order = ["critical", "high", "medium", "low", "informational"]
    for s in _order:
        if s in severities:
            return s
    return Severity.MEDIUM
