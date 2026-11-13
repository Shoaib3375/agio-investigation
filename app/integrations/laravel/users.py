"""Laravel internal API — user / account intelligence endpoints."""

from __future__ import annotations

from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.schemas import (
    UserActivityResponse,
    UserComplianceResponse,
    UserLedgerResponse,
    UserOrdersResponse,
    UserProfile,
    UserRiskSummaryResponse,
    UserWalletsResponse,
)


async def get_user_profile(client: LaravelClient, user_id: int) -> UserProfile:
    """GET /users/{id}/profile."""
    data = await client.get(f"/users/{user_id}/profile")
    return UserProfile.model_validate(data)


async def get_user_activity(client: LaravelClient, user_id: int) -> UserActivityResponse:
    """GET /users/{id}/activity — sessions, activity log, login attempts."""
    data = await client.get(f"/users/{user_id}/activity")
    return UserActivityResponse.model_validate(data)


async def get_user_risk_summary(
    client: LaravelClient, user_id: int
) -> UserRiskSummaryResponse:
    """GET /users/{id}/risk-summary — risk cases, screening, flagged orders."""
    data = await client.get(f"/users/{user_id}/risk-summary")
    return UserRiskSummaryResponse.model_validate(data)


async def get_user_orders(
    client: LaravelClient,
    user_id: int,
    *,
    page: int = 1,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    currency: str | None = None,
) -> UserOrdersResponse:
    """GET /users/{id}/orders — paginated order list.

    Parameters
    ----------
    page:
        Page number (1-based).
    status:
        Filter by order status.
    date_from / date_to:
        ISO date strings ``YYYY-MM-DD``.
    currency:
        Sell currency code filter.
    """
    params: dict[str, str | int] = {"page": page}
    if status:
        params["status"] = status
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if currency:
        params["currency"] = currency

    data = await client.get(f"/users/{user_id}/orders", params=params)
    return UserOrdersResponse.model_validate(data)


async def get_user_wallets(client: LaravelClient, user_id: int) -> UserWalletsResponse:
    """GET /users/{id}/wallets."""
    data = await client.get(f"/users/{user_id}/wallets")
    return UserWalletsResponse.model_validate(data)


async def get_user_ledger(client: LaravelClient, user_id: int) -> UserLedgerResponse:
    """GET /users/{id}/ledger — ledger accounts."""
    data = await client.get(f"/users/{user_id}/ledger")
    return UserLedgerResponse.model_validate(data)


async def get_user_compliance(
    client: LaravelClient, user_id: int
) -> UserComplianceResponse:
    """GET /users/{id}/compliance — sanitised risk + screening evidence."""
    data = await client.get(f"/users/{user_id}/compliance")
    return UserComplianceResponse.model_validate(data)
