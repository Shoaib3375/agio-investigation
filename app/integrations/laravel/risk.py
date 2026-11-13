"""Laravel internal API — risk and compliance endpoints."""

from __future__ import annotations

from app.integrations.laravel.client import LaravelClient
from app.integrations.laravel.schemas import RiskCaseDetailResponse


async def get_risk_case(
    client: LaravelClient, risk_case_uuid: str
) -> RiskCaseDetailResponse:
    """GET /risk-cases/{uuid} — risk case with associated screening results.

    Parameters
    ----------
    risk_case_uuid:
        Must be a valid UUID string. An invalid UUID returns 404 from Laravel.

    Notes
    -----
    The ``screening_results`` field is loaded via Eloquent ``with()`` and
    keyed as ``screeningResults`` in the raw JSON.  The schema uses
    ``screening_results`` with ``populate_by_name=True`` to handle both.
    """
    data = await client.get(f"/risk-cases/{risk_case_uuid}")
    # Laravel returns {"risk_case": {..., "screening_results": [...]}}
    # The nested screeningResults is already serialised as snake_case by
    # Eloquent's JSON output for this endpoint.
    return RiskCaseDetailResponse.model_validate(data)
