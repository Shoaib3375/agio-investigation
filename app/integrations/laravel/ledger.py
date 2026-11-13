"""Laravel internal API — financial ledger endpoints.

Financial comparisons must use ``Decimal`` — never ``float``.
Schemas use ``Decimal`` for all monetary fields.
"""

from __future__ import annotations

from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.schemas import (
    JournalEntryDetailResponse,
    OrderLedgerResponse,
    UserLedgerResponse,
)


async def get_order_ledger(
    client: LaravelClient, order_uuid: str
) -> OrderLedgerResponse:
    """GET /orders/{uuid}/ledger — journal entries and postings for an order."""
    data = await client.get(f"/orders/{order_uuid}/ledger")
    return OrderLedgerResponse.model_validate(data)


async def get_user_ledger(
    client: LaravelClient, user_id: int
) -> UserLedgerResponse:
    """GET /users/{id}/ledger — ledger accounts owned by a user."""
    data = await client.get(f"/users/{user_id}/ledger")
    return UserLedgerResponse.model_validate(data)


async def get_journal_entry(
    client: LaravelClient, journal_uuid: str
) -> JournalEntryDetailResponse:
    """GET /ledger/journal/{uuid} — single journal entry with postings.

    Parameters
    ----------
    journal_uuid:
        Must be a valid UUID string.  An invalid UUID returns a 404 from
        Laravel (guarded by ``invalidUuid()`` helper added in pre-M03 fix).
    """
    data = await client.get(f"/ledger/journal/{journal_uuid}")
    return JournalEntryDetailResponse.model_validate(data)
