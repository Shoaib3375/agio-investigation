"""Pydantic response schemas for every Laravel internal investigation API endpoint.

Schemas are strictly typed against the actual DB columns and resource classes
verified on 2026-11-13. All monetary fields arrive as decimal strings from
Laravel; use Python ``Decimal`` when doing arithmetic — never ``float``.

Naming follows the Laravel DB column names exactly so discrepancies are obvious.
Optional fields use ``None`` default; they may be absent from the JSON payload
when the underlying column is NULL.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / primitives
# ---------------------------------------------------------------------------


class PaginationLink(BaseModel):
    url: str | None = None
    label: str
    page: int | None = None
    active: bool


class PaginationMeta(BaseModel):
    current_page: int
    from_: int | None = Field(None, alias="from")
    last_page: int
    links: list[PaginationLink]
    path: str
    per_page: int
    to: int | None = None
    total: int

    model_config = {"populate_by_name": True}


class PaginationLinks(BaseModel):
    first: str | None = None
    last: str | None = None
    prev: str | None = None
    next: str | None = None


# ---------------------------------------------------------------------------
# User / account endpoints
# ---------------------------------------------------------------------------


class UserProfile(BaseModel):
    """GET /users/{id}/profile → InternalUserProfileResource."""

    id: int
    status: str
    kyc_status: str
    country: str
    email_verified_at: datetime | None = None
    two_factor_enabled_at: datetime | None = None
    account_type: str | None = None
    phone_verified_at: datetime | None = None
    created_at: datetime | None = None


class UserSession(BaseModel):
    ip_address: str | None = None
    device: str | None = None
    created_at: datetime | None = None
    revoked_at: datetime | None = None


class ActivityLogEntry(BaseModel):
    log_name: str | None = None
    description: str | None = None
    subject_type: str | None = None
    subject_id: int | None = None
    properties: dict[str, Any] | None = None
    created_at: datetime | None = None


class LoginAttempt(BaseModel):
    ip_address: str | None = None
    user_agent: str | None = None
    failure_reason: str | None = None
    attempted_at: datetime | None = None


class UserActivityResponse(BaseModel):
    """GET /users/{id}/activity."""

    user_sessions: list[UserSession]
    activity_log: list[ActivityLogEntry]
    login_attempts: list[LoginAttempt]


class RiskCase(BaseModel):
    """Shared — appears in risk-summary, compliance, and risk-cases detail."""

    id: int
    uuid: str
    user_id: int
    order_id: int | None = None
    reason: str
    severity: str  # low | medium | high | critical
    status: str    # open | investigating | customer_contacted | escalated | cleared | rejected
    reviewer_id: int | None = None
    notes: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ScreeningResult(BaseModel):
    """Shared — appears in risk-summary, compliance, and risk-cases detail."""

    id: int
    subject_type: str
    subject_id: int
    provider: str
    check_type: str
    result: str  # clear | review | match | error
    risk_score: Decimal | None = None
    notes: str | None = None
    risk_case_id: int | None = None
    screened_at: datetime
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FlaggedOrder(BaseModel):
    """Minimal order projection returned by userRiskSummary."""

    id: int
    uuid: str
    status: str
    risk_status: str
    sell_amount: Decimal
    buy_amount: Decimal
    created_at: datetime | None = None


class UserRiskSummaryResponse(BaseModel):
    """GET /users/{id}/risk-summary."""

    risk_cases: list[RiskCase]
    screening_results: list[ScreeningResult]
    flagged_orders: list[FlaggedOrder]


class OrderSummary(BaseModel):
    """InternalOrderResource — used in user orders list and order detail."""

    transaction_id: str  # order.uuid
    user_id: int
    sell_amount: Decimal
    buy_amount: Decimal
    fee_amount: Decimal
    rate: Decimal
    sell_currency: str | None = None
    buy_currency: str | None = None
    status: str
    risk_status: str
    reconciliation_status: str
    destination_address: str | None = None
    deposit_address: str | None = None
    deposit_txid: str | None = None
    deposit_confirmations: int = 0
    settlement_txid: str | None = None
    provider_id: int | None = None
    provider_execution_id: str | None = None
    idempotency_key: str | None = None
    created_from_ip: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    deposit_detected_at: datetime | None = None
    deposit_confirmed_at: datetime | None = None
    provider_executed_at: datetime | None = None


class UserOrdersResponse(BaseModel):
    """GET /users/{id}/orders — paginated InternalOrderResource collection."""

    data: list[OrderSummary]
    links: PaginationLinks
    meta: PaginationMeta


class WalletEntry(BaseModel):
    id: int
    user_id: int
    currency_id: int
    balance: Decimal
    frozen_balance: Decimal
    pending_balance: Decimal
    status: str  # active | frozen
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # currency relation (whenLoaded — may include code/name)
    currency: dict[str, Any] | None = None


class UserWalletsResponse(BaseModel):
    """GET /users/{id}/wallets."""

    wallets: list[WalletEntry]


class LedgerAccount(BaseModel):
    id: int
    owner_type: str
    owner_id: int
    currency_id: int
    account_type: str | None = None
    balance: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserLedgerResponse(BaseModel):
    """GET /users/{id}/ledger."""

    ledger_accounts: list[LedgerAccount]


class UserComplianceResponse(BaseModel):
    """GET /users/{id}/compliance."""

    risk_cases: list[RiskCase]
    screening_results: list[ScreeningResult]


# ---------------------------------------------------------------------------
# Order / transaction endpoints
# ---------------------------------------------------------------------------


class OrderDetailResponse(BaseModel):
    """GET /orders/{uuid} — wraps InternalOrderResource directly (no envelope key)."""

    # The resource is returned directly (not wrapped in {"order": ...})
    # so the top-level keys are the OrderSummary fields.
    transaction_id: str
    user_id: int
    sell_amount: Decimal
    buy_amount: Decimal
    fee_amount: Decimal
    rate: Decimal
    sell_currency: str | None = None
    buy_currency: str | None = None
    status: str
    risk_status: str
    reconciliation_status: str
    destination_address: str | None = None
    deposit_address: str | None = None
    deposit_txid: str | None = None
    deposit_confirmations: int = 0
    settlement_txid: str | None = None
    provider_id: int | None = None
    provider_execution_id: str | None = None
    idempotency_key: str | None = None
    created_from_ip: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    deposit_detected_at: datetime | None = None
    deposit_confirmed_at: datetime | None = None
    provider_executed_at: datetime | None = None


class OrderEvent(BaseModel):
    """Single entry from order_events table."""

    id: int
    from_status: str | None = None
    to_status: str
    actor_type: str
    actor_id: int | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class OrderTimelineResponse(BaseModel):
    """GET /orders/{uuid}/timeline."""

    order_events: list[OrderEvent]


class BlockchainDeposit(BaseModel):
    id: int
    order_id: int
    tx_hash: str
    network_identifier: str
    currency_code: str
    deposit_address: str
    received_amount: Decimal
    expected_amount: Decimal
    amount_difference: Decimal
    confirmations: int
    required_confirmations: int
    status: str  # detected | confirming | confirmed | mismatch | overpaid | underpaid
    from_address: str | None = None
    block_number: int | None = None
    detected_at: datetime
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # raw_data intentionally excluded — may contain provider secrets


class BlockchainPayout(BaseModel):
    id: int
    order_id: int
    tx_hash: str | None = None
    network_identifier: str | None = None
    currency_code: str | None = None
    payout_address: str | None = None
    amount: Decimal | None = None
    status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderBlockchainResponse(BaseModel):
    """GET /orders/{uuid}/blockchain."""

    blockchain_deposits: list[BlockchainDeposit]
    blockchain_payouts: list[BlockchainPayout]


class ProviderQuote(BaseModel):
    id: int
    quote_id: int | None = None
    provider_id: int | None = None
    sell_amount: Decimal | None = None
    buy_amount: Decimal | None = None
    rate: Decimal | None = None
    fee_amount: Decimal | None = None
    status: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProviderExecution(BaseModel):
    id: int
    order_id: int
    provider_id: int | None = None
    external_id: str | None = None
    status: str | None = None
    sell_amount: Decimal | None = None
    buy_amount: Decimal | None = None
    fee_amount: Decimal | None = None
    executed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderProviderResponse(BaseModel):
    """GET /orders/{uuid}/provider."""

    provider_executions: list[ProviderExecution]
    provider_quotes: list[ProviderQuote]


class LedgerPosting(BaseModel):
    id: int
    journal_entry_id: int
    ledger_account_id: int
    debit: Decimal
    credit: Decimal
    currency_id: int
    description: str | None = None
    created_at: datetime


class JournalEntry(BaseModel):
    id: int
    uuid: str
    reference_type: str
    reference_id: int
    description: str
    status: str  # pending | posted | reversed
    reversal_of: int | None = None
    actor_type: str
    actor_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    postings: list[LedgerPosting] = []


class OrderLedgerResponse(BaseModel):
    """GET /orders/{uuid}/ledger."""

    journal_entries: list[JournalEntry]


class RecoveryAction(BaseModel):
    id: int
    reconciliation_case_id: int | None = None
    order_id: int | None = None
    action_type: str | None = None
    status: str | None = None
    notes: str | None = None
    executed_by: int | None = None
    executed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReconciliationCase(BaseModel):
    id: int
    uuid: str
    order_id: int
    provider_identifier: str | None = None
    provider_transaction_id: str | None = None
    case_type: str
    severity: str
    expected_state: str | None = None
    actual_state: str | None = None
    description: str
    status: str
    assigned_to: int | None = None
    resolution: str | None = None
    resolved_by: int | None = None
    resolved_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderReconciliationResponse(BaseModel):
    """GET /orders/{uuid}/reconciliation."""

    reconciliation_cases: list[ReconciliationCase]
    recovery_actions: list[RecoveryAction]


# ---------------------------------------------------------------------------
# Financial endpoints
# ---------------------------------------------------------------------------


class JournalEntryDetailResponse(BaseModel):
    """GET /ledger/journal/{uuid}."""

    journal_entry: JournalEntry


# ---------------------------------------------------------------------------
# Deposit / withdrawal endpoints
# ---------------------------------------------------------------------------


class DepositRequestRecord(BaseModel):
    """GET /deposits/{id}."""

    id: int
    reference: str | None = None
    user_id: int
    currency_id: int
    amount: Decimal | None = None
    transaction_hash: str | None = None
    status: str | None = None
    admin_notes: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Loaded relations
    user: dict[str, Any] | None = None
    currency: dict[str, Any] | None = None


class DepositDetailResponse(BaseModel):
    """GET /deposits/{id}."""

    deposit: DepositRequestRecord


class WithdrawalRecord(BaseModel):
    id: int
    user_id: int
    currency_id: int
    amount: Decimal | None = None
    address: str | None = None
    status: str | None = None
    tx_hash: str | None = None
    admin_notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    user: dict[str, Any] | None = None
    currency: dict[str, Any] | None = None


class WithdrawalDetailResponse(BaseModel):
    """GET /withdrawals/{id}."""

    withdrawal: WithdrawalRecord


class BlockchainDepositDetailResponse(BaseModel):
    """GET /blockchain/deposits/{id}."""

    blockchain_deposit: BlockchainDeposit


# ---------------------------------------------------------------------------
# Risk / compliance endpoints
# ---------------------------------------------------------------------------


class RiskCaseDetailResponse(BaseModel):
    """GET /risk-cases/{uuid}."""

    risk_case: RiskCase
    # screeningResults loaded via with() — Pydantic accepts snake_case alias
    screening_results: list[ScreeningResult] = []

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Provider endpoints
# ---------------------------------------------------------------------------


class ProviderRecord(BaseModel):
    identifier: str
    name: str
    status: str
    circuit_open: bool
    degraded: bool
    risk_rating: str | None = None
    on_time_rate: Decimal | None = None


class ProviderDetailResponse(BaseModel):
    """GET /providers/{identifier}."""

    provider: ProviderRecord


class ProviderExecutionsResponse(BaseModel):
    """GET /providers/{identifier}/executions — paginated."""

    provider: str
    executions: dict[str, Any]  # paginated Laravel response; parse data items as ProviderExecution
