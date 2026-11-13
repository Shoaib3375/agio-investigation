"""High-level evidence client — composes all Laravel resource modules.

The ``EvidenceClient`` is the single entry-point the investigation workflow
uses to retrieve evidence. It wraps the per-resource modules, records
retrieval timing, and handles ``LaravelNotFoundError`` gracefully so callers
receive ``None`` instead of raising for optional evidence.

Usage::

    async with LaravelClient.from_settings(settings) as http:
        client = EvidenceClient(http)
        profile = await client.user_profile(user_id=6)
        timeline = await client.order_timeline(order_uuid="…")
"""

from __future__ import annotations

import time
from typing import TypeVar

import structlog

from app.integrations.laravel import (
    blockchain,
    ledger,
    orders,
    providers,
    reconciliation,
    risk,
    users,
)
from app.integrations.laravel.client import LaravelClient, LaravelNotFoundError
from app.integrations.laravel.schemas import (
    BlockchainDepositDetailResponse,
    DepositDetailResponse,
    JournalEntryDetailResponse,
    OrderBlockchainResponse,
    OrderDetailResponse,
    OrderLedgerResponse,
    OrderProviderResponse,
    OrderReconciliationResponse,
    OrderTimelineResponse,
    ProviderDetailResponse,
    ProviderExecutionsResponse,
    RiskCaseDetailResponse,
    UserActivityResponse,
    UserComplianceResponse,
    UserLedgerResponse,
    UserOrdersResponse,
    UserProfile,
    UserRiskSummaryResponse,
    UserWalletsResponse,
    WithdrawalDetailResponse,
)

logger = structlog.get_logger()

T = TypeVar("T")


class EvidenceClient:
    """Façade over all Laravel internal investigation API resource modules.

    Every method returns either the typed response object or ``None`` when
    the resource is not found (404). All other errors propagate.
    """

    def __init__(self, client: LaravelClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # User / account
    # ------------------------------------------------------------------

    async def user_profile(self, user_id: int) -> UserProfile | None:
        return await self._fetch(users.get_user_profile, self._client, user_id)

    async def user_activity(self, user_id: int) -> UserActivityResponse | None:
        return await self._fetch(users.get_user_activity, self._client, user_id)

    async def user_risk_summary(self, user_id: int) -> UserRiskSummaryResponse | None:
        return await self._fetch(users.get_user_risk_summary, self._client, user_id)

    async def user_orders(
        self,
        user_id: int,
        *,
        page: int = 1,
        status: str | None = None,
    ) -> UserOrdersResponse | None:
        return await self._fetch(
            users.get_user_orders,
            self._client,
            user_id,
            page=page,
            status=status,
        )

    async def user_wallets(self, user_id: int) -> UserWalletsResponse | None:
        return await self._fetch(users.get_user_wallets, self._client, user_id)

    async def user_ledger(self, user_id: int) -> UserLedgerResponse | None:
        return await self._fetch(users.get_user_ledger, self._client, user_id)

    async def user_compliance(self, user_id: int) -> UserComplianceResponse | None:
        return await self._fetch(users.get_user_compliance, self._client, user_id)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def order(self, order_uuid: str) -> OrderDetailResponse | None:
        return await self._fetch(orders.get_order, self._client, order_uuid)

    async def order_timeline(self, order_uuid: str) -> OrderTimelineResponse | None:
        return await self._fetch(orders.get_order_timeline, self._client, order_uuid)

    # ------------------------------------------------------------------
    # Blockchain
    # ------------------------------------------------------------------

    async def order_blockchain(self, order_uuid: str) -> OrderBlockchainResponse | None:
        return await self._fetch(blockchain.get_order_blockchain, self._client, order_uuid)

    async def blockchain_deposit(self, deposit_id: int) -> BlockchainDepositDetailResponse | None:
        return await self._fetch(blockchain.get_blockchain_deposit, self._client, deposit_id)

    async def deposit_request(self, deposit_id: int) -> DepositDetailResponse | None:
        return await self._fetch(blockchain.get_deposit_request, self._client, deposit_id)

    async def withdrawal(self, withdrawal_id: int) -> WithdrawalDetailResponse | None:
        return await self._fetch(blockchain.get_withdrawal, self._client, withdrawal_id)

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------

    async def order_provider(self, order_uuid: str) -> OrderProviderResponse | None:
        return await self._fetch(providers.get_order_provider, self._client, order_uuid)

    async def provider(self, identifier: str) -> ProviderDetailResponse | None:
        return await self._fetch(providers.get_provider, self._client, identifier)

    async def provider_executions(
        self,
        identifier: str,
        *,
        page: int = 1,
        status: str | None = None,
    ) -> ProviderExecutionsResponse | None:
        return await self._fetch(
            providers.get_provider_executions,
            self._client,
            identifier,
            page=page,
            status=status,
        )

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    async def order_ledger(self, order_uuid: str) -> OrderLedgerResponse | None:
        return await self._fetch(ledger.get_order_ledger, self._client, order_uuid)

    async def journal_entry(self, journal_uuid: str) -> JournalEntryDetailResponse | None:
        return await self._fetch(ledger.get_journal_entry, self._client, journal_uuid)

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    async def order_reconciliation(
        self, order_uuid: str
    ) -> OrderReconciliationResponse | None:
        return await self._fetch(
            reconciliation.get_order_reconciliation, self._client, order_uuid
        )

    # ------------------------------------------------------------------
    # Risk
    # ------------------------------------------------------------------

    async def risk_case(self, risk_case_uuid: str) -> RiskCaseDetailResponse | None:
        return await self._fetch(risk.get_risk_case, self._client, risk_case_uuid)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _fetch(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Call ``fn(*args, **kwargs)`` and return ``None`` on 404."""
        name = fn.__name__
        start = time.monotonic()
        try:
            result = await fn(*args, **kwargs)
            elapsed = time.monotonic() - start
            logger.debug("evidence.fetched", fn=name, elapsed_ms=round(elapsed * 1000, 1))
            return result
        except LaravelNotFoundError:
            elapsed = time.monotonic() - start
            logger.info(
                "evidence.not_found",
                fn=name,
                elapsed_ms=round(elapsed * 1000, 1),
            )
            return None
