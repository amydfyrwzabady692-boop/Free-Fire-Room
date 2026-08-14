from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import structlog
from fastapi import Request

from app.core.config import get_settings

SENSITIVE_KEYS = {
    "password",
    "room_password",
    "room_id",
    "token",
    "bot_token",
    "secret",
    "otp",
    "refresh_token",
    "access_token",
    "credentials",
    "totp",
    "hash",
}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_KEYS or "password" in str(k).lower() or "token" in str(k).lower():
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(i) for i in obj]
    return obj


def configure_logging() -> None:
    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _RedactProcessor(),
            structlog.processors.JSONRenderer()
            if settings.is_production
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


class _RedactProcessor:
    def __call__(self, _logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]:
        return _redact(event_dict)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def correlation_id_from_request(request: Request) -> str:
    existing = request.headers.get("x-correlation-id")
    return existing or str(uuid.uuid4())


def dumps_safe(data: Any) -> str:
    return json.dumps(_redact(data), default=str, ensure_ascii=False)
