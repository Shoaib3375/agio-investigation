"""Laravel internal API — order / transaction evidence endpoints."""

from __future__ import annotations

from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.schemas import (
    OrderDetailResponse,
    OrderTimelineResponse,
)


async def get_order(client: LaravelClient, order_uuid: str) -> OrderDetailResponse:
    """GET /orders/{uuid} — full order snapshot via InternalOrderResource.

    Returns an ``OrderDetailResponse`` whose fields map directly to the
    InternalOrderResource keys. The resource is returned at the top level
    (not wrapped in ``{"order": ...}``).
    """
    data = await client.get(f"/orders/{order_uuid}")
    return OrderDetailResponse.model_validate(data)


async def get_order_timeline(
    client: LaravelClient, order_uuid: str
) -> OrderTimelineResponse:
    """GET /orders/{uuid}/timeline — append-only lifecycle event list.

    Events are ordered ascending by ``created_at`` by Laravel.
    Do NOT sort further by RabbitMQ delivery order — use the ``created_at``
    timestamps from this response as the authoritative timeline.
    """
    data = await client.get(f"/orders/{order_uuid}/timeline")
    return OrderTimelineResponse.model_validate(data)
