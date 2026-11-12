"""Async SQLAlchemy engine, session factory, and FastAPI dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.investigation_database_url,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session, closing it when the request ends."""
    async with async_session_factory() as session:
        yield session


async def ping_database() -> bool:
    """Return True if the database accepts a SELECT 1, False otherwise."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
