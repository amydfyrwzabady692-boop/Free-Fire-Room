from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

def _pool_kwargs(url: str) -> dict:
    """Pool sizing is meaningless for SQLite, and StaticPool rejects it outright.

    Tests run on sqlite://, so passing these unconditionally makes every module
    that imports this one fail to import at all.
    """
    if url.startswith("sqlite"):
        return {}
    return {
        "pool_pre_ping": True,
        "pool_size": 1,
        "max_overflow": 0,
        "pool_recycle": 1800,
        "pool_timeout": 10,
    }


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug and not settings.is_production,
    **_pool_kwargs(settings.database_url),
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_sync_sessionmaker = None


def SyncSessionLocal() -> Session:
    global _sync_sessionmaker
    if _sync_sessionmaker is None:
        sync_engine = create_engine(
            settings.database_sync_url,
            **_pool_kwargs(settings.database_sync_url),
        )
        _sync_sessionmaker = sessionmaker(sync_engine, class_=Session, expire_on_commit=False)
    return _sync_sessionmaker()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
