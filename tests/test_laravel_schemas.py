"""PY-M03 contract tests — Pydantic schema validation.

These tests are pure Python: no network, no database, no Docker required.
They verify that the response shapes observed from the live Laravel API
parse correctly into our Pydantic models.

Fixtures are JSON-exact copies of real API responses captured on 2026-11-13
against the investigation service user (id=6) on a clean database.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.integrations.laravel.schemas import (
    BlockchainDeposit,
    JournalEntry,
    LedgerPosting,
    OrderDetailResponse,
    OrderEvent,
    OrderTimelineResponse,
    RiskCase,
    ScreeningResult,
    UserActivityResponse,
    UserComplianceResponse,
    UserLedgerResponse,
    UserOrdersResponse,
    UserProfile,
    UserRiskSummaryResponse,
    UserWalletsResponse,
)

# ---------------------------------------------------------------------------
# Fixtures — JSON-exact copies of live API responses
# ---------------------------------------------------------------------------

_USER_PROFILE = {
    "id": 6,
    "status": "active",
    "kyc_status": "none",
    "country": "XX",
    "email_verified_at": None,
    "two_factor_enabled_at": None,
    "account_type": "individual",
    "phone_verified_at": None,
    "created_at": "2026-11-12T20:40:25Z",
}

_USER_ACTIVITY_EMPTY = {
    "user_sessions": [],
    "activity_log": [],
    "login_attempts": [],
}

_USER_RISK_SUMMARY_EMPTY = {
    "risk_cases": [],
    "screening_results": [],
    "flagged_orders": [],
}

_USER_ORDERS_EMPTY = {
    "data": [],
    "links": {
        "first": "http://localhost/api/internal/investigation/v1/users/6/orders?page=1",
        "last": "http://localhost/api/internal/investigation/v1/users/6/orders?page=1",
        "prev": None,
        "next": None,
    },
    "meta": {
        "current_page": 1,
        "from": None,
        "last_page": 1,
        "links": [
            {"url": None, "label": "\u00ab Previous", "page": None, "active": False},
            {
                "url": "http://localhost/api/internal/investigation/v1/users/6/orders?page=1",
                "label": "1",
                "page": 1,
                "active": True,
            },
            {"url": None, "label": "Next \u00bb", "page": None, "active": False},
        ],
        "path": "http://localhost/api/internal/investigation/v1/users/6/orders",
        "per_page": 20,
        "to": None,
        "total": 0,
    },
}

_USER_WALLETS_EMPTY = {"wallets": []}
_USER_LEDGER_EMPTY = {"ledger_accounts": []}
_USER_COMPLIANCE_EMPTY = {"risk_cases": [], "screening_results": []}

# Synthetic order detail matching InternalOrderResource field names
_ORDER_DETAIL = {
    "transaction_id": "00000000-0000-0000-0000-000000000001",
    "user_id": 6,
    "sell_amount": "1.00000000",
    "buy_amount": "0.00005000",
    "fee_amount": "0.00000100",
    "rate": "20000.00000000",
    "sell_currency": "USDT",
    "buy_currency": "BTC",
    "status": "completed",
    "risk_status": "cleared",
    "reconciliation_status": "matched",
    "destination_address": "bc1qexample",
    "deposit_address": "TExampleTron",
    "deposit_txid": "abc123",
    "deposit_confirmations": 6,
    "settlement_txid": "def456",
    "provider_id": 1,
    "provider_execution_id": "prov-exec-001",
    "idempotency_key": "idem-001",
    "created_from_ip": "127.0.0.1",
    "created_at": "2026-11-13T10:00:00Z",
    "completed_at": "2026-11-13T10:05:00Z",
    "deposit_detected_at": "2026-11-13T10:01:00Z",
    "deposit_confirmed_at": "2026-11-13T10:02:00Z",
    "provider_executed_at": "2026-11-13T10:03:00Z",
}

_ORDER_TIMELINE = {
    "order_events": [
        {
            "id": 1,
            "from_status": None,
            "to_status": "awaiting_deposit",
            "actor_type": "system",
            "actor_id": None,
            "reason": None,
            "metadata": None,
            "created_at": "2026-11-13T10:00:00Z",
        },
        {
            "id": 2,
            "from_status": "awaiting_deposit",
            "to_status": "deposit_detected",
            "actor_type": "system",
            "actor_id": None,
            "reason": "tatum_webhook",
            "metadata": {"tx_hash": "abc123"},
            "created_at": "2026-11-13T10:01:00Z",
        },
    ]
}

_JOURNAL_ENTRY_WITH_POSTINGS = {
    "id": 1,
    "uuid": "00000000-0000-0000-0000-000000000002",
    "reference_type": "order",
    "reference_id": 1,
    "description": "Exchange fee",
    "status": "posted",
    "reversal_of": None,
    "actor_type": "system",
    "actor_id": None,
    "created_at": "2026-11-13T10:05:00Z",
    "updated_at": "2026-11-13T10:05:00Z",
    "postings": [
        {
            "id": 1,
            "journal_entry_id": 1,
            "ledger_account_id": 10,
            "debit": "0.00000100",
            "credit": "0.00000000",
            "currency_id": 1,
            "description": "Platform fee",
            "created_at": "2026-11-13T10:05:00Z",
        },
        {
            "id": 2,
            "journal_entry_id": 1,
            "ledger_account_id": 11,
            "debit": "0.00000000",
            "credit": "0.00000100",
            "currency_id": 1,
            "description": None,
            "created_at": "2026-11-13T10:05:00Z",
        },
    ],
}

_RISK_CASE = {
    "id": 1,
    "uuid": "00000000-0000-0000-0000-000000000003",
    "user_id": 6,
    "order_id": None,
    "reason": "Suspicious login pattern",
    "severity": "medium",
    "status": "open",
    "reviewer_id": None,
    "notes": None,
    "resolved_at": None,
    "created_at": "2026-11-13T09:00:00Z",
    "updated_at": "2026-11-13T09:00:00Z",
}

_SCREENING_RESULT = {
    "id": 1,
    "subject_type": "user",
    "subject_id": 6,
    "provider": "internal",
    "check_type": "sanctions",
    "result": "clear",
    "risk_score": "0.05",
    "notes": None,
    "risk_case_id": None,
    "screened_at": "2026-11-13T09:00:00Z",
    "created_at": "2026-11-13T09:00:00Z",
    "updated_at": "2026-11-13T09:00:00Z",
}

_BLOCKCHAIN_DEPOSIT = {
    "id": 1,
    "order_id": 1,
    "tx_hash": "0xabc123",
    "network_identifier": "tron",
    "currency_code": "USDT",
    "deposit_address": "TExampleTron",
    "received_amount": "1.00000000",
    "expected_amount": "1.00000000",
    "amount_difference": "0.00000000",
    "confirmations": 20,
    "required_confirmations": 20,
    "status": "confirmed",
    "from_address": "TSenderAddress",
    "block_number": 50000000,
    "detected_at": "2026-11-13T10:01:00Z",
    "confirmed_at": "2026-11-13T10:02:00Z",
    "created_at": "2026-11-13T10:01:00Z",
    "updated_at": "2026-11-13T10:02:00Z",
}

# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------


def test_user_profile_valid():
    profile = UserProfile.model_validate(_USER_PROFILE)
    assert profile.id == 6
    assert profile.status == "active"
    assert profile.kyc_status == "none"
    assert profile.country == "XX"
    assert profile.email_verified_at is None
    assert profile.account_type == "individual"


def test_user_profile_missing_optional_fields():
    """Optional datetime fields may be absent from the payload."""
    minimal = {
        "id": 7,
        "status": "inactive",
        "kyc_status": "pending",
        "country": "GB",
    }
    profile = UserProfile.model_validate(minimal)
    assert profile.created_at is None


def test_user_profile_requires_id():
    with pytest.raises(ValidationError):
        UserProfile.model_validate({"status": "active", "kyc_status": "none", "country": "XX"})


# ---------------------------------------------------------------------------
# User activity
# ---------------------------------------------------------------------------


def test_user_activity_empty():
    r = UserActivityResponse.model_validate(_USER_ACTIVITY_EMPTY)
    assert r.user_sessions == []
    assert r.activity_log == []
    assert r.login_attempts == []


# ---------------------------------------------------------------------------
# User risk summary
# ---------------------------------------------------------------------------


def test_user_risk_summary_empty():
    r = UserRiskSummaryResponse.model_validate(_USER_RISK_SUMMARY_EMPTY)
    assert r.risk_cases == []
    assert r.screening_results == []
    assert r.flagged_orders == []


# ---------------------------------------------------------------------------
# User orders (paginated)
# ---------------------------------------------------------------------------


def test_user_orders_empty_pagination():
    r = UserOrdersResponse.model_validate(_USER_ORDERS_EMPTY)
    assert r.data == []
    assert r.meta.current_page == 1
    assert r.meta.total == 0
    assert r.meta.per_page == 20
    # from_ aliases "from" in the JSON
    assert r.meta.from_ is None


# ---------------------------------------------------------------------------
# User wallets / ledger / compliance
# ---------------------------------------------------------------------------


def test_user_wallets_empty():
    r = UserWalletsResponse.model_validate(_USER_WALLETS_EMPTY)
    assert r.wallets == []


def test_user_ledger_empty():
    r = UserLedgerResponse.model_validate(_USER_LEDGER_EMPTY)
    assert r.ledger_accounts == []


def test_user_compliance_empty():
    r = UserComplianceResponse.model_validate(_USER_COMPLIANCE_EMPTY)
    assert r.risk_cases == []
    assert r.screening_results == []


# ---------------------------------------------------------------------------
# Order detail
# ---------------------------------------------------------------------------


def test_order_detail_decimal_fields():
    """Monetary fields must parse as Decimal, not float."""
    r = OrderDetailResponse.model_validate(_ORDER_DETAIL)
    assert isinstance(r.sell_amount, Decimal)
    assert isinstance(r.buy_amount, Decimal)
    assert isinstance(r.fee_amount, Decimal)
    assert isinstance(r.rate, Decimal)
    assert r.transaction_id == "00000000-0000-0000-0000-000000000001"
    assert r.status == "completed"
    assert r.deposit_confirmations == 6


def test_order_detail_optional_nulls():
    """destination_address and similar may be None."""
    minimal = {**_ORDER_DETAIL, "destination_address": None, "deposit_txid": None}
    r = OrderDetailResponse.model_validate(minimal)
    assert r.destination_address is None
    assert r.deposit_txid is None


# ---------------------------------------------------------------------------
# Order timeline
# ---------------------------------------------------------------------------


def test_order_timeline_events():
    r = OrderTimelineResponse.model_validate(_ORDER_TIMELINE)
    assert len(r.order_events) == 2
    first = r.order_events[0]
    assert isinstance(first, OrderEvent)
    assert first.from_status is None
    assert first.to_status == "awaiting_deposit"
    second = r.order_events[1]
    assert second.metadata == {"tx_hash": "abc123"}


# ---------------------------------------------------------------------------
# Journal entry with postings
# ---------------------------------------------------------------------------


def test_journal_entry_postings():
    entry = JournalEntry.model_validate(_JOURNAL_ENTRY_WITH_POSTINGS)
    assert entry.status == "posted"
    assert len(entry.postings) == 2
    debit_posting = entry.postings[0]
    assert isinstance(debit_posting, LedgerPosting)
    assert isinstance(debit_posting.debit, Decimal)
    assert debit_posting.debit == Decimal("0.00000100")
    assert debit_posting.credit == Decimal("0.00000000")


def test_journal_entry_postings_decimal_sum():
    """Verify double-entry: sum of debits == sum of credits using Decimal arithmetic."""
    entry = JournalEntry.model_validate(_JOURNAL_ENTRY_WITH_POSTINGS)
    total_debit = sum(p.debit for p in entry.postings)
    total_credit = sum(p.credit for p in entry.postings)
    assert total_debit == total_credit  # 0.000001 == 0.000001


# ---------------------------------------------------------------------------
# Risk case
# ---------------------------------------------------------------------------


def test_risk_case_fields():
    rc = RiskCase.model_validate(_RISK_CASE)
    assert rc.uuid == "00000000-0000-0000-0000-000000000003"
    assert rc.severity == "medium"
    assert rc.status == "open"
    assert rc.order_id is None


# ---------------------------------------------------------------------------
# Screening result
# ---------------------------------------------------------------------------


def test_screening_result_decimal_risk_score():
    sr = ScreeningResult.model_validate(_SCREENING_RESULT)
    assert sr.result == "clear"
    assert isinstance(sr.risk_score, Decimal)
    assert sr.risk_score == Decimal("0.05")


def test_screening_result_null_risk_score():
    data = {**_SCREENING_RESULT, "risk_score": None}
    sr = ScreeningResult.model_validate(data)
    assert sr.risk_score is None


# ---------------------------------------------------------------------------
# Blockchain deposit
# ---------------------------------------------------------------------------


def test_blockchain_deposit_amounts_are_decimal():
    dep = BlockchainDeposit.model_validate(_BLOCKCHAIN_DEPOSIT)
    assert isinstance(dep.received_amount, Decimal)
    assert isinstance(dep.expected_amount, Decimal)
    assert isinstance(dep.amount_difference, Decimal)
    assert dep.amount_difference == Decimal("0.00000000")
    assert dep.status == "confirmed"


def test_blockchain_deposit_mismatch_detection():
    """Non-zero amount_difference indicates a deposit mismatch."""
    mismatch = {**_BLOCKCHAIN_DEPOSIT, "received_amount": "0.95000000", "amount_difference": "-0.05000000", "status": "underpaid"}
    dep = BlockchainDeposit.model_validate(mismatch)
    assert dep.received_amount < dep.expected_amount
    assert dep.status == "underpaid"
