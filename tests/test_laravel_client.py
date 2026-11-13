"""PY-M03 integration tests — live Laravel API calls.

These tests require:
  - Laravel app running on http://localhost:8000
  - A valid LARAVEL_SERVICE_TOKEN in the environment (or .env)
  - The investigation service user (id=6) to exist

Skip all if the API is unreachable or the token is not configured.

Run only these tests:
    pytest tests/test_laravel_client.py -v -m requires_laravel
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.integrations.laravel.client import (
    LaravelAuthError,
    LaravelClient,
    LaravelNotFoundError,
)
from app.integrations.laravel.evidence_client import EvidenceClient
from app.integrations.laravel.schemas import (
    UserActivityResponse,
    UserComplianceResponse,
    UserLedgerResponse,
    UserOrdersResponse,
    UserProfile,
    UserRiskSummaryResponse,
    UserWalletsResponse,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_LARAVEL_URL = os.environ.get(
    "LARAVEL_INTERNAL_API_URL",
    "http://localhost:8000/api/internal/investigation/v1",
)
_SERVICE_TOKEN = os.environ.get("LARAVEL_SERVICE_TOKEN", "")
_TEST_USER_ID = int(os.environ.get("LARAVEL_TEST_USER_ID", "6"))


# ---------------------------------------------------------------------------
# Reachability check
# ---------------------------------------------------------------------------


async def _api_reachable() -> bool:
    if not _SERVICE_TOKEN:
        return False
    try:
        async with LaravelClient(
            base_url=_LARAVEL_URL,
            service_token=_SERVICE_TOKEN,
            timeout_seconds=5,
            max_retries=0,
        ) as client:
            await client.get(f"/users/{_TEST_USER_ID}/profile")
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def laravel_client():
    """Yield a live LaravelClient, skip if the API is not reachable."""
    if not await _api_reachable():
        pytest.skip("Laravel internal API not reachable or token not configured")
    async with LaravelClient(
        base_url=_LARAVEL_URL,
        service_token=_SERVICE_TOKEN,
        timeout_seconds=10,
        max_retries=1,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def evidence(laravel_client: LaravelClient) -> EvidenceClient:
    return EvidenceClient(laravel_client)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.requires_laravel
async def test_invalid_token_raises_auth_error():
    """A bad token must raise LaravelAuthError, not a generic exception."""
    async with LaravelClient(
        base_url=_LARAVEL_URL,
        service_token="bad-token",
        timeout_seconds=5,
        max_retries=0,
    ) as client:
        with pytest.raises(LaravelAuthError):
            await client.get(f"/users/{_TEST_USER_ID}/profile")


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------


@pytest.mark.requires_laravel
async def test_user_profile_live(laravel_client: LaravelClient):
    data = await laravel_client.get(f"/users/{_TEST_USER_ID}/profile")
    profile = UserProfile.model_validate(data)
    assert profile.id == _TEST_USER_ID
    assert profile.status in ("active", "inactive", "suspended")
    assert isinstance(profile.kyc_status, str)


@pytest.mark.requires_laravel
async def test_evidence_client_user_profile(evidence: EvidenceClient):
    profile = await evidence.user_profile(_TEST_USER_ID)
    assert profile is not None
    assert profile.id == _TEST_USER_ID


# ---------------------------------------------------------------------------
# User activity / risk / wallets / ledger / compliance
# ---------------------------------------------------------------------------


@pytest.mark.requires_laravel
async def test_user_activity_live(laravel_client: LaravelClient):
    data = await laravel_client.get(f"/users/{_TEST_USER_ID}/activity")
    r = UserActivityResponse.model_validate(data)
    assert isinstance(r.user_sessions, list)
    assert isinstance(r.login_attempts, list)


@pytest.mark.requires_laravel
async def test_user_risk_summary_live(laravel_client: LaravelClient):
    data = await laravel_client.get(f"/users/{_TEST_USER_ID}/risk-summary")
    r = UserRiskSummaryResponse.model_validate(data)
    assert isinstance(r.risk_cases, list)
    assert isinstance(r.flagged_orders, list)


@pytest.mark.requires_laravel
async def test_user_orders_paginated_live(laravel_client: LaravelClient):
    data = await laravel_client.get(f"/users/{_TEST_USER_ID}/orders")
    r = UserOrdersResponse.model_validate(data)
    assert r.meta.current_page == 1
    assert r.meta.per_page == 20
    assert isinstance(r.data, list)


@pytest.mark.requires_laravel
async def test_user_wallets_live(laravel_client: LaravelClient):
    data = await laravel_client.get(f"/users/{_TEST_USER_ID}/wallets")
    r = UserWalletsResponse.model_validate(data)
    assert isinstance(r.wallets, list)


@pytest.mark.requires_laravel
async def test_user_ledger_live(laravel_client: LaravelClient):
    data = await laravel_client.get(f"/users/{_TEST_USER_ID}/ledger")
    r = UserLedgerResponse.model_validate(data)
    assert isinstance(r.ledger_accounts, list)


@pytest.mark.requires_laravel
async def test_user_compliance_live(laravel_client: LaravelClient):
    data = await laravel_client.get(f"/users/{_TEST_USER_ID}/compliance")
    r = UserComplianceResponse.model_validate(data)
    assert isinstance(r.risk_cases, list)
    assert isinstance(r.screening_results, list)


# ---------------------------------------------------------------------------
# Not-found handling
# ---------------------------------------------------------------------------


@pytest.mark.requires_laravel
async def test_nonexistent_user_raises_not_found(laravel_client: LaravelClient):
    """A non-existent user must return 404 → LaravelNotFoundError."""
    with pytest.raises(LaravelNotFoundError):
        await laravel_client.get("/users/999999/profile")


@pytest.mark.requires_laravel
async def test_evidence_client_returns_none_for_missing(evidence: EvidenceClient):
    """EvidenceClient.user_profile must return None (not raise) for 404."""
    result = await evidence.user_profile(999999)
    assert result is None


@pytest.mark.requires_laravel
async def test_invalid_uuid_order_returns_not_found(laravel_client: LaravelClient):
    """An invalid UUID string must return 404 (not 500) after the pre-M03 fix."""
    with pytest.raises(LaravelNotFoundError):
        await laravel_client.get("/orders/not-a-valid-uuid")


@pytest.mark.requires_laravel
async def test_invalid_uuid_journal_returns_not_found(laravel_client: LaravelClient):
    with pytest.raises(LaravelNotFoundError):
        await laravel_client.get("/ledger/journal/not-a-valid-uuid")


@pytest.mark.requires_laravel
async def test_invalid_uuid_risk_case_returns_not_found(laravel_client: LaravelClient):
    with pytest.raises(LaravelNotFoundError):
        await laravel_client.get("/risk-cases/not-a-valid-uuid")


# ---------------------------------------------------------------------------
# EvidenceClient — not-found returns None for all resource types
# ---------------------------------------------------------------------------


@pytest.mark.requires_laravel
async def test_evidence_client_order_none(evidence: EvidenceClient):
    r = await evidence.order("00000000-0000-0000-0000-000000000000")
    assert r is None


@pytest.mark.requires_laravel
async def test_evidence_client_order_timeline_none(evidence: EvidenceClient):
    r = await evidence.order_timeline("00000000-0000-0000-0000-000000000000")
    assert r is None


@pytest.mark.requires_laravel
async def test_evidence_client_risk_case_none(evidence: EvidenceClient):
    r = await evidence.risk_case("00000000-0000-0000-0000-000000000000")
    assert r is None


@pytest.mark.requires_laravel
async def test_evidence_client_journal_none(evidence: EvidenceClient):
    r = await evidence.journal_entry("00000000-0000-0000-0000-000000000000")
    assert r is None
