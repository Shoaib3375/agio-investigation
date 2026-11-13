"""Shared pytest fixtures and test bootstrap."""

import os

# Settings required before the app is imported. INVESTIGATION_DATABASE_URL is
# mandatory for Settings; the readiness test skips if the DB is unreachable.
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault(
    "INVESTIGATION_DATABASE_URL",
    "postgresql+asyncpg://investigation:local_password@127.0.0.1:5437/agio_investigation",
)

# ---------------------------------------------------------------------------
# Patch the app engine to use NullPool in the test environment.
# NullPool never reuses connections across tests, preventing asyncpg from
# trying to return connections to a closed event loop (Windows/Python 3.13
# proactor issue). Must happen BEFORE the app module is imported.
# ---------------------------------------------------------------------------
import sqlalchemy.pool  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

_original_create_async_engine = create_async_engine


def _nullpool_create_async_engine(url, **kwargs):
    """Force NullPool in test environments to prevent loop-closed errors."""
    kwargs.setdefault("poolclass", sqlalchemy.pool.NullPool)
    return _original_create_async_engine(url, **kwargs)


# Monkey-patch so both the app and test fixtures use NullPool
import sqlalchemy.ext.asyncio as _sqla_async  # noqa: E402
_sqla_async.create_async_engine = _nullpool_create_async_engine  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Now safe to import the app
# ---------------------------------------------------------------------------
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """Yield an async HTTP client bound to the FastAPI app (no lifespan)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
