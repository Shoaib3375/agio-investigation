"""Laravel internal API — blockchain evidence endpoints."""

from __future__ import annotations

from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.schemas import (
    BlockchainDepositDetailResponse,
    DepositDetailResponse,
    OrderBlockchainResponse,
    WithdrawalDetailResponse,
)


async def get_order_blockchain(
    client: LaravelClient, order_uuid: str
) -> OrderBlockchainResponse:
    """GET /orders/{uuid}/blockchain — deposits and payouts for an order."""
    data = await client.get(f"/orders/{order_uuid}/blockchain")
    return OrderBlockchainResponse.model_validate(data)


async def get_blockchain_deposit(
    client: LaravelClient, deposit_id: int
) -> BlockchainDepositDetailResponse:
    """GET /blockchain/deposits/{id} — single blockchain deposit record."""
    data = await client.get(f"/blockchain/deposits/{deposit_id}")
    return BlockchainDepositDetailResponse.model_validate(data)


async def get_deposit_request(
    client: LaravelClient, deposit_id: int
) -> DepositDetailResponse:
    """GET /deposits/{id} — DepositRequest record (manual deposit workflow)."""
    data = await client.get(f"/deposits/{deposit_id}")
    return DepositDetailResponse.model_validate(data)


async def get_withdrawal(
    client: LaravelClient, withdrawal_id: int
) -> WithdrawalDetailResponse:
    """GET /withdrawals/{id} — WithdrawRequest record."""
    data = await client.get(f"/withdrawals/{withdrawal_id}")
    return WithdrawalDetailResponse.model_validate(data)
