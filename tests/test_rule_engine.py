"""PY-M05 tests — deterministic rule engine.

All analyzer tests are pure-unit (no DB, no network).
RuleEngine DB integration tests require the investigation database (requires_db).

Coverage:
  - base helpers: to_decimal, parse_datetime, hours_since, RuleContext
  - order_analyzer: all 7 rules
  - blockchain_analyzer: all 5 rules
  - provider_analyzer: all 5 rules
  - ledger_analyzer: all 4 rules
  - account_analyzer: all 5 rules
  - RuleEngine: build context, dispatch, persist, deduplicate
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.investigation.rules import (
    account_analyzer,
    blockchain_analyzer,
    ledger_analyzer,
    order_analyzer,
    provider_analyzer,
)
from app.investigation.rules.base import (
    RuleContext,
    Severity,
    hours_since,
    parse_datetime,
    to_decimal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=UTC)


def _hours_ago(h: float) -> str:
    return (_now() - timedelta(hours=h)).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _ctx(
    external_reference: str | None = None,
    entity_type: str = "order",
    **kwargs,
) -> RuleContext:
    ctx = RuleContext(
        investigation_id=_uuid(),
        external_reference=external_reference or _uuid(),
        entity_type=entity_type,
    )
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


# ===========================================================================
# Base helpers
# ===========================================================================


class TestToDecimal:
    def test_string(self):
        assert to_decimal("1.23456789") == Decimal("1.23456789")

    def test_int(self):
        assert to_decimal(5) == Decimal("5")

    def test_none_returns_default(self):
        assert to_decimal(None) == Decimal("0")
        assert to_decimal(None, Decimal("99")) == Decimal("99")

    def test_invalid_returns_default(self):
        assert to_decimal("not-a-number") == Decimal("0")


class TestParseDatetime:
    def test_zulu(self):
        dt = parse_datetime("2026-11-13T10:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_offset(self):
        dt = parse_datetime("2026-11-13T10:00:00+00:00")
        assert dt is not None

    def test_none(self):
        assert parse_datetime(None) is None

    def test_empty(self):
        assert parse_datetime("") is None

    def test_invalid(self):
        assert parse_datetime("not-a-date") is None


class TestHoursSince:
    def test_recent(self):
        dt = _now() - timedelta(hours=2)
        h = hours_since(dt)
        assert 1.9 < h < 2.1

    def test_none(self):
        assert hours_since(None) is None

    def test_naive_treated_as_utc(self):
        naive = (_now() - timedelta(hours=1)).replace(tzinfo=None)
        h = hours_since(naive)
        assert h is not None
        assert 0.9 < h < 1.1


# ===========================================================================
# Order analyzer
# ===========================================================================


class TestOrderAnalyzer:
    def test_no_findings_for_clean_completed_order(self):
        ctx = _ctx(
            order={
                "transaction_id": _uuid(),
                "status": "completed",
                "created_at": _hours_ago(1),
                "deposit_confirmations": 6,
                "destination_address": "addr1",
            },
            timeline={"order_events": [
                {"to_status": "awaiting_deposit", "created_at": _hours_ago(1)},
                {"to_status": "completed", "created_at": _hours_ago(0.1)},
            ]},
            blockchain={
                "blockchain_deposits": [{"id": 1, "status": "confirmed"}],
                "blockchain_payouts": [{"id": 1}],
            },
        )
        results = order_analyzer.analyze(ctx)
        assert results == []

    def test_missing_order_evidence(self):
        ctx = _ctx(order=None)
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_EVIDENCE_MISSING" in codes

    def test_awaiting_deposit_timeout(self):
        ctx = _ctx(
            order={
                "status": "awaiting_deposit",
                "created_at": _hours_ago(3),
            }
        )
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_AWAITING_DEPOSIT_TIMEOUT" in codes

    def test_awaiting_deposit_not_yet_timeout(self):
        ctx = _ctx(
            order={"status": "awaiting_deposit", "created_at": _hours_ago(0.5)}
        )
        results = order_analyzer.analyze(ctx)
        assert not any(r.finding_code == "ORDER_AWAITING_DEPOSIT_TIMEOUT" for r in results)

    def test_deposit_unconfirmed_timeout(self):
        ctx = _ctx(
            order={
                "status": "deposit_detected",
                "created_at": _hours_ago(5),
                "deposit_detected_at": _hours_ago(5),
            }
        )
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_DEPOSIT_UNCONFIRMED_TIMEOUT" in codes

    def test_amount_mismatch_status(self):
        ctx = _ctx(order={"status": "amount_mismatch", "created_at": _hours_ago(1)})
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_AMOUNT_MISMATCH" in codes
        assert results[0].severity == Severity.HIGH

    def test_reconciliation_required(self):
        ctx = _ctx(order={"status": "reconciliation_required", "created_at": _hours_ago(1)})
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_RECONCILIATION_REQUIRED" in codes

    def test_stuck_intermediate_status(self):
        ctx = _ctx(
            order={
                "status": "risk_review",
                "created_at": _hours_ago(10),
            },
            timeline={"order_events": [
                {"to_status": "risk_review", "created_at": _hours_ago(10)},
            ]},
        )
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_STUCK_INTERMEDIATE_STATUS" in codes

    def test_completed_missing_deposit_evidence(self):
        ctx = _ctx(
            order={"status": "completed", "created_at": _hours_ago(1)},
            blockchain={"blockchain_deposits": [], "blockchain_payouts": [{"id": 1}]},
        )
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_COMPLETED_MISSING_DEPOSIT_EVIDENCE" in codes

    def test_failed_no_timeline_events(self):
        ctx = _ctx(
            order={"status": "failed", "created_at": _hours_ago(1)},
            timeline={"order_events": []},
        )
        results = order_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ORDER_FAILED_NO_TIMELINE" in codes


# ===========================================================================
# Blockchain analyzer
# ===========================================================================


class TestBlockchainAnalyzer:
    def test_no_findings_for_clean_deposit(self):
        ctx = _ctx(
            order={"status": "completed", "destination_address": "addr1"},
            blockchain={
                "blockchain_deposits": [{
                    "id": 1,
                    "received_amount": "1.00000000",
                    "expected_amount": "1.00000000",
                    "amount_difference": "0.00000000",
                    "confirmations": 20,
                    "required_confirmations": 20,
                    "status": "confirmed",
                    "currency_code": "USDT",
                    "tx_hash": "0xabc",
                    "network_identifier": "tron",
                    "detected_at": _hours_ago(1),
                }],
                "blockchain_payouts": [{"id": 1, "payout_address": "addr1"}],
            },
        )
        results = blockchain_analyzer.analyze(ctx)
        assert results == []

    def test_no_blockchain_evidence(self):
        ctx = _ctx(blockchain=None)
        results = blockchain_analyzer.analyze(ctx)
        assert results == []

    def test_underpaid(self):
        ctx = _ctx(
            blockchain={
                "blockchain_deposits": [{
                    "id": 1,
                    "received_amount": "0.95000000",
                    "expected_amount": "1.00000000",
                    "status": "underpaid",
                    "currency_code": "USDT",
                    "tx_hash": "0xabc",
                    "confirmations": 1,
                    "required_confirmations": 1,
                    "detected_at": _hours_ago(1),
                }],
                "blockchain_payouts": [],
            },
        )
        results = blockchain_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "BLOCKCHAIN_DEPOSIT_UNDERPAID" in codes
        underpaid = next(r for r in results if r.finding_code == "BLOCKCHAIN_DEPOSIT_UNDERPAID")
        assert underpaid.severity == Severity.HIGH

    def test_overpaid(self):
        ctx = _ctx(
            blockchain={
                "blockchain_deposits": [{
                    "id": 1,
                    "received_amount": "1.10000000",
                    "expected_amount": "1.00000000",
                    "status": "overpaid",
                    "currency_code": "USDT",
                    "tx_hash": "0xabc",
                    "confirmations": 1,
                    "required_confirmations": 1,
                    "detected_at": _hours_ago(1),
                }],
                "blockchain_payouts": [],
            },
        )
        results = blockchain_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "BLOCKCHAIN_DEPOSIT_OVERPAID" in codes
        overpaid = next(r for r in results if r.finding_code == "BLOCKCHAIN_DEPOSIT_OVERPAID")
        assert overpaid.severity == Severity.MEDIUM

    def test_confirmation_delay(self):
        ctx = _ctx(
            blockchain={
                "blockchain_deposits": [{
                    "id": 1,
                    "received_amount": "1.00000000",
                    "expected_amount": "1.00000000",
                    "status": "confirming",
                    "currency_code": "BTC",
                    "tx_hash": "0xbtc",
                    "confirmations": 1,
                    "required_confirmations": 6,
                    "network_identifier": "bitcoin",
                    "detected_at": _hours_ago(3),
                }],
                "blockchain_payouts": [],
            },
        )
        results = blockchain_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "BLOCKCHAIN_CONFIRMATION_DELAY" in codes

    def test_duplicate_tx_hash(self):
        ctx = _ctx(
            blockchain={
                "blockchain_deposits": [
                    {"id": 1, "received_amount": "1.0", "expected_amount": "1.0",
                     "status": "confirmed", "tx_hash": "0xdup", "confirmations": 1,
                     "required_confirmations": 1, "detected_at": _hours_ago(1)},
                    {"id": 2, "received_amount": "1.0", "expected_amount": "1.0",
                     "status": "confirmed", "tx_hash": "0xdup", "confirmations": 1,
                     "required_confirmations": 1, "detected_at": _hours_ago(0.5)},
                ],
                "blockchain_payouts": [],
            },
        )
        results = blockchain_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "BLOCKCHAIN_DUPLICATE_TX_HASH" in codes

    def test_payout_address_mismatch(self):
        ctx = _ctx(
            order={"status": "completed", "destination_address": "addr_correct"},
            blockchain={
                "blockchain_deposits": [],
                "blockchain_payouts": [{"id": 1, "payout_address": "addr_wrong"}],
            },
        )
        results = blockchain_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "BLOCKCHAIN_PAYOUT_ADDRESS_MISMATCH" in codes
        mismatch = next(r for r in results if r.finding_code == "BLOCKCHAIN_PAYOUT_ADDRESS_MISMATCH")
        assert mismatch.severity == Severity.CRITICAL

    def test_no_confirmed_deposit_on_completed_order(self):
        ctx = _ctx(
            order={"status": "completed"},
            blockchain={
                "blockchain_deposits": [{"id": 1, "received_amount": "1.0",
                    "expected_amount": "1.0", "status": "confirming",
                    "tx_hash": "0x1", "confirmations": 2,
                    "required_confirmations": 6, "detected_at": _hours_ago(1)}],
                "blockchain_payouts": [],
            },
        )
        results = blockchain_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "BLOCKCHAIN_NO_CONFIRMED_DEPOSIT" in codes


# ===========================================================================
# Provider analyzer
# ===========================================================================


class TestProviderAnalyzer:
    def test_no_provider_evidence(self):
        ctx = _ctx(provider=None)
        results = provider_analyzer.analyze(ctx)
        assert results == []

    def test_no_executions(self):
        ctx = _ctx(provider={"provider_executions": [], "provider_quotes": []})
        results = provider_analyzer.analyze(ctx)
        assert results == []

    def test_execution_pending_timeout(self):
        ctx = _ctx(
            order={"status": "provider_execution_pending", "sell_amount": "1.0"},
            provider={
                "provider_executions": [{
                    "id": 1, "status": "pending",
                    "created_at": _hours_ago(2), "sell_amount": "1.0",
                }],
                "provider_quotes": [],
            },
        )
        results = provider_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "PROVIDER_EXECUTION_PENDING_TIMEOUT" in codes

    def test_execution_amount_mismatch(self):
        ctx = _ctx(
            order={"status": "provider_executed", "sell_amount": "1.00000000"},
            provider={
                "provider_executions": [{
                    "id": 1, "status": "completed",
                    "sell_amount": "0.95000000", "created_at": _hours_ago(0.1),
                }],
                "provider_quotes": [],
            },
        )
        results = provider_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "PROVIDER_EXECUTION_AMOUNT_MISMATCH" in codes

    def test_single_failed_execution(self):
        ctx = _ctx(
            order={"status": "failed", "sell_amount": "1.0"},
            provider={
                "provider_executions": [{
                    "id": 1, "status": "failed", "created_at": _hours_ago(1),
                }],
                "provider_quotes": [],
            },
        )
        results = provider_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "PROVIDER_EXECUTION_FAILED" in codes

    def test_repeated_failed_executions(self):
        ctx = _ctx(
            order={"status": "failed", "sell_amount": "1.0"},
            provider={
                "provider_executions": [
                    {"id": 1, "status": "failed", "created_at": _hours_ago(2)},
                    {"id": 2, "status": "failed", "created_at": _hours_ago(1)},
                ],
                "provider_quotes": [],
            },
        )
        results = provider_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "PROVIDER_REPEATED_EXECUTION_FAILURES" in codes

    def test_completed_no_successful_execution(self):
        ctx = _ctx(
            order={"status": "completed", "sell_amount": "1.0"},
            provider={
                "provider_executions": [
                    {"id": 1, "status": "failed", "created_at": _hours_ago(1)},
                ],
                "provider_quotes": [],
            },
        )
        results = provider_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "PROVIDER_COMPLETED_ORDER_NO_EXECUTION" in codes


# ===========================================================================
# Ledger analyzer
# ===========================================================================


class TestLedgerAnalyzer:
    def test_no_findings_for_posted_balanced_journal(self):
        ctx = _ctx(
            order={"status": "completed"},
            ledger={
                "journal_entries": [{
                    "id": 1, "uuid": _uuid(),
                    "status": "posted",
                    "reference_type": "order",
                    "description": "Exchange fee",
                    "postings": [
                        {"debit": "0.00100000", "credit": "0.00000000", "currency_id": 1},
                        {"debit": "0.00000000", "credit": "0.00100000", "currency_id": 1},
                    ],
                }]
            },
        )
        results = ledger_analyzer.analyze(ctx)
        assert results == []

    def test_non_completed_order_skipped(self):
        ctx = _ctx(
            order={"status": "awaiting_deposit"},
            ledger={"journal_entries": []},
        )
        results = ledger_analyzer.analyze(ctx)
        assert results == []

    def test_missing_ledger_evidence(self):
        ctx = _ctx(order={"status": "completed"}, ledger=None)
        results = ledger_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "LEDGER_EVIDENCE_MISSING" in codes

    def test_no_journal_entries(self):
        ctx = _ctx(
            order={"status": "completed"},
            ledger={"journal_entries": []},
        )
        results = ledger_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "LEDGER_MISSING_JOURNAL" in codes

    def test_reversed_journal(self):
        ctx = _ctx(
            order={"status": "completed"},
            ledger={
                "journal_entries": [{
                    "id": 1, "uuid": _uuid(), "status": "reversed",
                    "description": "Reversed fee", "postings": [],
                }]
            },
        )
        results = ledger_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "LEDGER_JOURNAL_REVERSED" in codes
        assert next(r for r in results if r.finding_code == "LEDGER_JOURNAL_REVERSED").severity == Severity.HIGH

    def test_pending_journal(self):
        ctx = _ctx(
            order={"status": "completed"},
            ledger={
                "journal_entries": [{
                    "id": 1, "uuid": _uuid(), "status": "pending",
                    "description": "Pending fee", "postings": [],
                }]
            },
        )
        results = ledger_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "LEDGER_JOURNAL_PENDING" in codes

    def test_posting_imbalance(self):
        ctx = _ctx(
            order={"status": "completed"},
            ledger={
                "journal_entries": [{
                    "id": 1, "uuid": _uuid(), "status": "posted",
                    "description": "Imbalanced entry",
                    "postings": [
                        {"debit": "1.00000000", "credit": "0.00000000", "currency_id": 1},
                        {"debit": "0.00000000", "credit": "0.50000000", "currency_id": 1},
                    ],
                }]
            },
        )
        results = ledger_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "LEDGER_POSTING_IMBALANCE" in codes
        assert next(r for r in results if r.finding_code == "LEDGER_POSTING_IMBALANCE").severity == Severity.CRITICAL

    def test_posting_exact_balance(self):
        """Exact balance must NOT trigger the imbalance rule."""
        ctx = _ctx(
            order={"status": "completed"},
            ledger={
                "journal_entries": [{
                    "id": 1, "uuid": _uuid(), "status": "posted",
                    "description": "Balanced",
                    "postings": [
                        {"debit": "0.00000001", "credit": "0.00000000", "currency_id": 1},
                        {"debit": "0.00000000", "credit": "0.00000001", "currency_id": 1},
                    ],
                }]
            },
        )
        results = ledger_analyzer.analyze(ctx)
        assert not any(r.finding_code == "LEDGER_POSTING_IMBALANCE" for r in results)


# ===========================================================================
# Account analyzer
# ===========================================================================


class TestAccountAnalyzer:
    def test_no_account_evidence(self):
        ctx = _ctx(account=None, risk=None, compliance=None)
        results = account_analyzer.analyze(ctx)
        assert results == []

    def test_no_findings_for_clean_account(self):
        ctx = _ctx(
            account={"status": "active", "kyc_status": "approved"},
            risk={"risk_cases": [], "screening_results": [], "flagged_orders": []},
            compliance={"risk_cases": [], "screening_results": []},
        )
        results = account_analyzer.analyze(ctx)
        assert results == []

    def test_multiple_open_risk_cases(self):
        ctx = _ctx(
            account={"status": "active", "kyc_status": "approved"},
            risk={
                "risk_cases": [
                    {"status": "open", "severity": "medium"},
                    {"status": "investigating", "severity": "high"},
                ],
                "screening_results": [],
                "flagged_orders": [],
            },
            compliance={"risk_cases": [], "screening_results": []},
        )
        results = account_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ACCOUNT_MULTIPLE_OPEN_RISK_CASES" in codes

    def test_kyc_not_approved(self):
        ctx = _ctx(
            account={"status": "active", "kyc_status": "pending"},
            risk={"risk_cases": [], "screening_results": [], "flagged_orders": []},
            compliance={"risk_cases": [], "screening_results": []},
        )
        results = account_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ACCOUNT_KYC_NOT_APPROVED" in codes

    def test_kyc_none_not_approved(self):
        ctx = _ctx(
            account={"status": "active", "kyc_status": "none"},
            risk={"risk_cases": [], "screening_results": [], "flagged_orders": []},
            compliance={"risk_cases": [], "screening_results": []},
        )
        results = account_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ACCOUNT_KYC_NOT_APPROVED" in codes

    def test_account_suspended(self):
        ctx = _ctx(
            account={"status": "suspended", "kyc_status": "approved"},
            risk={"risk_cases": [], "screening_results": [], "flagged_orders": []},
            compliance={"risk_cases": [], "screening_results": []},
        )
        results = account_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ACCOUNT_STATUS_RESTRICTED" in codes
        assert next(r for r in results if r.finding_code == "ACCOUNT_STATUS_RESTRICTED").severity == Severity.HIGH

    def test_stub_screening_result(self):
        ctx = _ctx(
            account={"status": "active", "kyc_status": "approved"},
            risk={
                "risk_cases": [],
                "screening_results": [{
                    "provider": "internal",
                    "result": "clear",
                    "risk_score": "0.00",
                    "check_type": "sanctions",
                }],
                "flagged_orders": [],
            },
            compliance={"risk_cases": [], "screening_results": []},
        )
        results = account_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ACCOUNT_STUB_SCREENING_RESULT" in codes

    def test_flagged_orders(self):
        ctx = _ctx(
            account={"status": "active", "kyc_status": "approved"},
            risk={
                "risk_cases": [],
                "screening_results": [],
                "flagged_orders": [
                    {"uuid": _uuid(), "risk_status": "flagged", "status": "completed"},
                    {"uuid": _uuid(), "risk_status": "rejected", "status": "failed"},
                ],
            },
            compliance={"risk_cases": [], "screening_results": []},
        )
        results = account_analyzer.analyze(ctx)
        codes = [r.finding_code for r in results]
        assert "ACCOUNT_HAS_FLAGGED_ORDERS" in codes


# ===========================================================================
# RuleEngine DB integration (requires_db)
# ===========================================================================

_DB_URL = os.environ.get(
    "INVESTIGATION_DATABASE_URL",
    "postgresql+asyncpg://investigation:local_password@127.0.0.1:5437/agio_investigation",
)


async def _db_reachable() -> bool:
    engine = create_async_engine(_DB_URL, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    if not await _db_reachable():
        pytest.skip("investigation database not reachable on 5437")
    engine = create_async_engine(_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.requires_db
async def test_rule_engine_no_evidence_no_findings(db_session: AsyncSession):
    """RuleEngine with no evidence records produces no findings."""
    from app.database.repositories import investigation_repository
    from app.investigation.rules.engine import RuleEngine

    inv = await investigation_repository.create(
        db_session,
        external_reference=_uuid(),
        entity_type="order",
    )
    await db_session.commit()

    engine = RuleEngine(db_session)
    findings = await engine.analyze(
        investigation_id=inv.id,
        external_reference=inv.external_reference,
        entity_type=inv.entity_type,
    )
    await db_session.commit()

    # No evidence → only ORDER_EVIDENCE_MISSING should trigger
    assert len(findings) == 1
    assert findings[0].finding_code == "ORDER_EVIDENCE_MISSING"
    assert findings[0].source == "deterministic_rule"


@pytest.mark.requires_db
async def test_rule_engine_persists_findings_from_evidence(db_session: AsyncSession):
    """RuleEngine stores findings derived from stored evidence snapshots."""
    from app.database.repositories import (
        evidence_record_repository,
        investigation_repository,
    )
    from app.investigation.evidence_collector import _content_hash
    from app.investigation.rules.engine import RuleEngine
    from app.models.evidence_record import EvidenceRecord

    inv = await investigation_repository.create(
        db_session,
        external_reference=_uuid(),
        entity_type="order",
    )
    await db_session.commit()

    # Store an order snapshot with status=amount_mismatch
    order_snapshot = {
        "transaction_id": inv.external_reference,
        "status": "amount_mismatch",
        "created_at": _hours_ago(1),
        "deposit_confirmations": 0,
    }
    rec = EvidenceRecord(
        investigation_id=inv.id,
        evidence_type="order",
        source_record_type="order",
        source_record_id=inv.external_reference,
        retrieved_at=_now(),
        content_hash=_content_hash(order_snapshot),
        is_verified=True,
        verification_method="schema_match",
        snapshot=order_snapshot,
    )
    saved_rec = await evidence_record_repository.insert(db_session, rec)
    await db_session.commit()

    engine = RuleEngine(db_session)
    findings = await engine.analyze(
        investigation_id=inv.id,
        external_reference=inv.external_reference,
        entity_type=inv.entity_type,
    )
    await db_session.commit()

    codes = [f.finding_code for f in findings]
    assert "ORDER_AMOUNT_MISMATCH" in codes

    # Verify evidence_ids reference the stored record
    mismatch_finding = next(f for f in findings if f.finding_code == "ORDER_AMOUNT_MISMATCH")
    assert saved_rec.id in mismatch_finding.evidence_ids


@pytest.mark.requires_db
async def test_rule_engine_deduplicates_on_rerun(db_session: AsyncSession):
    """Running RuleEngine twice on the same investigation does not create duplicate findings."""
    from app.database.repositories import (
        evidence_record_repository,
        finding_repository,
        investigation_repository,
    )
    from app.investigation.evidence_collector import _content_hash
    from app.investigation.rules.engine import RuleEngine
    from app.models.evidence_record import EvidenceRecord

    inv = await investigation_repository.create(
        db_session,
        external_reference=_uuid(),
        entity_type="order",
    )
    await db_session.commit()

    snapshot = {"transaction_id": inv.external_reference, "status": "amount_mismatch",
                "created_at": _hours_ago(1), "deposit_confirmations": 0}
    rec = EvidenceRecord(
        investigation_id=inv.id,
        evidence_type="order",
        source_record_type="order",
        source_record_id=inv.external_reference,
        retrieved_at=_now(),
        content_hash=_content_hash(snapshot),
        is_verified=True,
        snapshot=snapshot,
    )
    await evidence_record_repository.insert(db_session, rec)
    await db_session.commit()

    engine = RuleEngine(db_session)
    first_run = await engine.analyze(
        investigation_id=inv.id,
        external_reference=inv.external_reference,
        entity_type=inv.entity_type,
    )
    await db_session.commit()
    first_count = len(first_run)

    second_run = await engine.analyze(
        investigation_id=inv.id,
        external_reference=inv.external_reference,
        entity_type=inv.entity_type,
    )
    await db_session.commit()

    # Second run should return 0 new findings (all already persisted)
    assert len(second_run) == 0

    # Total in DB unchanged
    all_findings = await finding_repository.get_by_investigation(db_session, inv.id)
    assert len(all_findings) == first_count


@pytest.mark.requires_db
async def test_rule_engine_blockchain_underpaid_finding(db_session: AsyncSession):
    """RuleEngine detects underpaid deposit from blockchain evidence snapshot."""
    from app.database.repositories import (
        evidence_record_repository,
        investigation_repository,
    )
    from app.investigation.evidence_collector import _content_hash
    from app.investigation.rules.engine import RuleEngine
    from app.models.evidence_record import EvidenceRecord

    inv = await investigation_repository.create(
        db_session,
        external_reference=_uuid(),
        entity_type="order",
    )
    await db_session.commit()

    order_snap = {"transaction_id": inv.external_reference, "status": "deposit_detected",
                  "created_at": _hours_ago(1), "deposit_confirmations": 1}
    bc_snap = {
        "blockchain_deposits": [{
            "id": 1,
            "received_amount": "0.90000000",
            "expected_amount": "1.00000000",
            "status": "underpaid",
            "currency_code": "USDT",
            "tx_hash": "0xunderpaid",
            "confirmations": 20,
            "required_confirmations": 20,
            "detected_at": _hours_ago(1),
        }],
        "blockchain_payouts": [],
    }

    for ev_type, snap in [("order", order_snap), ("blockchain", bc_snap)]:
        rec = EvidenceRecord(
            investigation_id=inv.id,
            evidence_type=ev_type,
            source_record_type=ev_type,
            source_record_id=inv.external_reference,
            retrieved_at=_now(),
            content_hash=_content_hash(snap),
            is_verified=True,
            snapshot=snap,
        )
        await evidence_record_repository.insert(db_session, rec)
    await db_session.commit()

    engine = RuleEngine(db_session)
    findings = await engine.analyze(
        investigation_id=inv.id,
        external_reference=inv.external_reference,
        entity_type=inv.entity_type,
    )
    await db_session.commit()

    codes = [f.finding_code for f in findings]
    assert "BLOCKCHAIN_DEPOSIT_UNDERPAID" in codes
    underpaid = next(f for f in findings if f.finding_code == "BLOCKCHAIN_DEPOSIT_UNDERPAID")
    assert underpaid.severity == Severity.HIGH
    assert underpaid.source == "deterministic_rule"
    assert underpaid.rule_version == "1.0.0"
