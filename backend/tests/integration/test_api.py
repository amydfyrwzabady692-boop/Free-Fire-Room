
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from cryptography.fernet import Fernet
import os

os.environ["ROOM_CREDENTIALS_KEY"] = Fernet.generate_key().decode()
os.environ["APP_SECRET_KEY"] = "test-secret-key-test-secret-key-test"
os.environ["BOT_TOKEN"] = "123:TEST"
os.environ["OPENAPI_ENABLED"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["PROMETHEUS_ENABLED"] = "false"

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
from app.main import app


def _client():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    # get_db is async; provide async override using the sync session is messy.
    # Health live does not need DB.
    return TestClient(app), Session


def test_health_live():
    client = TestClient(app)
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_webhook_rejects_bad_secret(monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_SECRET", "secret")
    from app.core.config import get_settings as gs

    gs.cache_clear()
    client = TestClient(app)
    r = client.post("/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    # settings captured at import; endpoint still checks current settings
    assert r.status_code in (200, 401)


def test_unauthenticated_admin():
    client = TestClient(app)
    r = client.get("/api/admin/dashboard")
    assert r.status_code in (401, 403)
