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

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """Yield an async HTTP client bound to the FastAPI app (no lifespan)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
