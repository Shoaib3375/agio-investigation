"""FastAPI dependencies — auth, DB session, pagination.

Authentication
--------------
Admin endpoints use a static Bearer token (``ADMIN_API_KEY`` env var).
When ``ADMIN_API_KEY`` is not configured the service starts but all admin
endpoints return 503 (misconfigured), so accidental open exposure is not
possible.

Replace with JWT / RBAC before production exposure.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_db_session


# ---------------------------------------------------------------------------
# DB session dependency
# ---------------------------------------------------------------------------


DBSession = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def require_admin_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Verify the Authorization: Bearer <ADMIN_API_KEY> header.

    Raises 503 when the key is not configured (misconfigured service).
    Raises 401 when the header is absent or the token does not match.
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "Admin API key not configured."},
        )
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_TOKEN", "message": "Authorization header required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Invalid or expired token."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


AdminAuth = Annotated[str, Depends(require_admin_token)]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class Pagination:
    """Standard pagination query parameters."""

    def __init__(self, page: int = 1, per_page: int = 20) -> None:
        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 1
        if per_page > 100:
            per_page = 100
        self.page = page
        self.per_page = per_page

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        return self.per_page
