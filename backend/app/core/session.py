from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=1,
    max_overflow=0,
    pool_recycle=1800,
    pool_timeout=10,
    echo=settings.debug and not settings.is_production,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_sync_sessionmaker = None


def SyncSessionLocal() -> Session:
    global _sync_sessionmaker
    if _sync_sessionmaker is None:
        sync_engine = create_engine(
            settings.database_sync_url,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            pool_recycle=1800,
            pool_timeout=10,
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
