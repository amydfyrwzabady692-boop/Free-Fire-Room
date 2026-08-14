from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")
settings = get_settings()


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def create_access_token(subject: str, extra: dict[str, Any] | None = None, minutes: int | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=minutes or settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire, "type": "access", **(extra or {})}
    return jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, jti: str) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    payload = {"sub": subject, "exp": expire, "type": "refresh", "jti": jti}
    return jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("invalid_token") from exc


def _fernet() -> Fernet:
    key = settings.fernet_key
    if not key:
        raise RuntimeError("ROOM_CREDENTIALS_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("decrypt_failed") from exc


def generate_unguessable_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def generate_otp(digits: int = 6) -> str:
    n = secrets.randbelow(10**digits)
    return str(n).zfill(digits)


def hmac_sha256(key: str, msg: str) -> str:
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


def verify_telegram_login(payload: dict[str, Any]) -> bool:
    """Verify Telegram Login Widget hash. https://core.telegram.org/widgets/login"""
    received = payload.get("hash")
    if not received or not settings.bot_token:
        return False
    data_check = "\n".join(
        f"{k}={v}" for k, v in sorted(payload.items()) if k != "hash" and v is not None
    )
    secret = hashlib.sha256(settings.bot_token.encode()).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def parse_telegram_auth_query(query: str) -> dict[str, str]:
    return dict(parse_qsl(query, keep_blank_values=True))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
