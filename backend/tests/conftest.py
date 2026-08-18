from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("ROOM_CREDENTIALS_KEY", Fernet.generate_key().decode())
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-test-secret-key-test")
os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("DATABASE_SYNC_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb(_type, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def _uuid(_type, compiler, **kw):
    return "CHAR(36)"


from app.core.db import Base
from app import models  # noqa: F401


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_c, _):
        dbapi_c.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db(engine) -> Session:
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    yield s
    s.close()


def make_user(db: Session, telegram_id: int, **kwargs):
    from app.models.user import User, UserProfile

    u = User(telegram_id=telegram_id, first_name=f"U{telegram_id}", **kwargs)
    db.add(u)
    db.flush()
    db.add(UserProfile(user_id=u.id))
    db.flush()
    return u


def make_organizer(db: Session, user):
    from app.models.organizer import Organizer

    o = Organizer(user_id=user.id, status="approved", display_name=user.first_name, verified_badge=True)
    db.add(o)
    db.flush()
    return o


def make_event(db: Session, organizer, capacity=2, **kwargs):
    from app.core.security import generate_unguessable_token
    from app.models.event import Event

    now = datetime.now(UTC)
    e = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=organizer.id,
        title=kwargs.get("title", "Custom"),
        starts_at=now + timedelta(hours=3),
        registration_ends_at=now + timedelta(hours=2),
        credentials_send_at=now + timedelta(hours=2, minutes=30),
        capacity=capacity,
        status=kwargs.get("status", "published"),
        required_referrals=kwargs.get("required_referrals", 0),
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        prize_summary=kwargs.get("prize_summary"),
        banner_file_id=kwargs.get("banner_file_id"),
    )
    db.add(e)
    db.flush()
    return e
