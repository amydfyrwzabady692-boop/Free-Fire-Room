from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routers import all_routers
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, correlation_id_from_request, get_logger
from app.core.redis import close_redis, get_redis
from app.core.session import engine

log = get_logger("api")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("api_startup")
    get_redis()
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
    yield
    await close_redis()
    await engine.dispose()


openapi_url = "/api/openapi.json" if (settings.openapi_enabled and not settings.is_production) else None
docs_url = "/api/docs" if openapi_url else None

app = FastAPI(
    title="Free Fire Room API",
    version="1.0.0",
    lifespan=lifespan,
    openapi_url=openapi_url,
    docs_url=docs_url,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-Id"],
)

@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = correlation_id_from_request(request)
    request.state.correlation_id = cid
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = cid
    return response


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    from sqlalchemy import text
    from app.core.session import SessionLocal

    async with SessionLocal() as db:
        await db.execute(text("SELECT 1"))
    await get_redis().ping()
    return {"status": "ready"}


@app.post(settings.webhook_path)
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = None):
    if settings.telegram_mode != "webhook":
        return {"ok": True, "ignored": True}
    if x_telegram_bot_api_secret_token != settings.webhook_secret:
        return JSONResponse({"ok": False}, status_code=401)
    from aiogram.types import Update
    from app.bot.loader import get_bot, get_dispatcher

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": get_bot()})
    await get_dispatcher().feed_update(get_bot(), update)
    return {"ok": True}


for r in all_routers():
    app.include_router(r, prefix="/api")

if settings.prometheus_enabled:
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_gzip=True,
            excluded_handlers=["/health/live", "/health/ready"],
        ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except Exception:
        log.exception("prometheus_init_failed")
