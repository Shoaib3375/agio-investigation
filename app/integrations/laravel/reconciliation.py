"""Laravel internal API — reconciliation evidence endpoints."""

from __future__ import annotations

from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.schemas import OrderReconciliationResponse


async def get_order_reconciliation(
    client: LaravelClient, order_uuid: str
) -> OrderReconciliationResponse:
    """GET /orders/{uuid}/reconciliation — cases and recovery actions for an order."""
    data = await client.get(f"/orders/{order_uuid}/reconciliation")
    return OrderReconciliationResponse.model_validate(data)
