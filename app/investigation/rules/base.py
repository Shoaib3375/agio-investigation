"""Rule engine base types — RuleResult, RuleContext, constants.

All rules are deterministic and pure-Python. They operate on dicts loaded
from EvidenceRecord.snapshot (already-validated, stored JSON). They return
RuleResult objects — never free-form LLM text.

Financial comparisons use Decimal. Never use float for money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class Severity:
    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Category:
    ORDER = "order"
    BLOCKCHAIN = "blockchain"
    PROVIDER = "provider"
    LEDGER = "ledger"
    ACCOUNT = "account"
    COMPLIANCE = "compliance"
    FINANCIAL = "financial"
    RECONCILIATION = "reconciliation"


class RuleSource:
    DETERMINISTIC = "deterministic_rule"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RuleResult:
    """The output of evaluating one rule against available evidence.

    Fields
    ------
    triggered:
        True when the rule detected an anomaly and a finding should be created.
    finding_code:
        Stable machine-readable code (e.g. ``"BLOCKCHAIN_AMOUNT_MISMATCH"``).
    category:
        One of the Category constants.
    severity:
        One of the Severity constants.
    title:
        Short human-readable label for the finding.
    description:
        Full explanation grounded in evidence.
    evidence_ids:
        List of EvidenceRecord UUIDs that support this finding.
    rule_version:
        Version string — increment when rule logic changes so reports can
        be traced to a specific evaluation implementation.
    confidence:
        Optional Decimal in [0.0, 1.0]. Not an AML probability score.
    """
    triggered: bool
    finding_code: str
    category: str
    severity: str
    title: str
    description: str
    evidence_ids: list[str] = field(default_factory=list)
    rule_version: str = "1.0.0"
    confidence: Decimal | None = None


# ---------------------------------------------------------------------------
# Rule context
# ---------------------------------------------------------------------------


@dataclass
class RuleContext:
    """Evidence snapshots passed to every analyzer.

    Each field is the parsed snapshot dict from the matching EvidenceRecord,
    or None if that evidence was not retrieved (missing/unavailable).

    ``evidence_ids`` maps evidence_type → EvidenceRecord UUID so findings
    can reference the supporting records.
    """
    investigation_id: str
    external_reference: str       # order UUID, user id, etc.
    entity_type: str              # order | user | withdrawal | risk_case

    # Evidence snapshots — set from EvidenceRecord.snapshot by RuleEngine
    order: dict[str, Any] | None = None
    timeline: dict[str, Any] | None = None
    blockchain: dict[str, Any] | None = None
    provider: dict[str, Any] | None = None
    ledger: dict[str, Any] | None = None
    reconciliation: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    compliance: dict[str, Any] | None = None
    wallet: dict[str, Any] | None = None

    # Maps evidence_type → evidence_record UUID
    evidence_ids: dict[str, str] = field(default_factory=dict)

    # Evaluation timestamp (UTC) — used by timeout rules
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def evidence_id_list(self, *types: str) -> list[str]:
        """Return a list of evidence_record UUIDs for the given evidence types."""
        return [self.evidence_ids[t] for t in types if t in self.evidence_ids]


# ---------------------------------------------------------------------------
# Decimal helpers
# ---------------------------------------------------------------------------


def to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Safely convert a value to Decimal. Returns default on failure."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string to a timezone-aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, AttributeError):
        return None


def hours_since(dt: datetime | None, now: datetime | None = None) -> float | None:
    """Return float hours elapsed since dt, or None if dt is None."""
    if dt is None:
        return None
    reference = now or datetime.now(tz=UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = reference - dt
    return delta.total_seconds() / 3600
