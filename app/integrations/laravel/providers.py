"""Laravel internal API — provider intelligence endpoints."""

from __future__ import annotations

from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.schemas import (
    OrderProviderResponse,
    ProviderDetailResponse,
    ProviderExecution,
    ProviderExecutionsResponse,
)


async def get_order_provider(
    client: LaravelClient, order_uuid: str
) -> OrderProviderResponse:
    """GET /orders/{uuid}/provider — executions and quotes for an order."""
    data = await client.get(f"/orders/{order_uuid}/provider")
    return OrderProviderResponse.model_validate(data)


async def get_provider(
    client: LaravelClient, identifier: str
) -> ProviderDetailResponse:
    """GET /providers/{identifier} — provider status and circuit breaker state."""
    data = await client.get(f"/providers/{identifier}")
    return ProviderDetailResponse.model_validate(data)


async def get_provider_executions(
    client: LaravelClient,
    identifier: str,
    *,
    page: int = 1,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> ProviderExecutionsResponse:
    """GET /providers/{identifier}/executions — paginated execution history.

    Parameters
    ----------
    identifier:
        Provider identifier string (e.g. ``"changenow"``).
    page:
        Page number (1-based).
    status:
        Filter by execution status.
    date_from / date_to:
        ISO date strings ``YYYY-MM-DD``.
    """
    params: dict[str, str | int] = {"page": page}
    if status:
        params["status"] = status
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    data = await client.get(f"/providers/{identifier}/executions", params=params)
    return ProviderExecutionsResponse.model_validate(data)
