from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "Free Fire Room"
    app_secret_key: str = "dev-only-change-me"
    debug: bool = False
    log_level: str = "INFO"
    default_timezone: str = "Asia/Tehran"
    default_locale: str = "fa"

    public_base_url: str = "http://localhost:8080"
    api_base_url: str = "http://localhost:8080/api"
    frontend_base_url: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000,http://localhost:8080"

    bot_token: str = ""
    bot_username: str = ""
    telegram_mode: Literal["polling", "webhook"] = "polling"
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str = "change-me-webhook-secret"
    telegram_login_bot_domain: str = "localhost"

    bootstrap_superadmin_telegram_id: int | None = None
    bootstrap_superadmin_password: str = ""

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ffroom"
    postgres_user: str = "ffroom"
    postgres_password: str = "ffroom"
    database_url: str = "postgresql+asyncpg://ffroom:ffroom@localhost:5432/ffroom"
    database_sync_url: str = "postgresql+psycopg://ffroom:ffroom@localhost:5432/ffroom"

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    room_credentials_key: str = ""
    backup_encryption_key: str = ""

    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 20
    refresh_token_expire_days: int = 7
    admin_session_idle_minutes: int = 30
    otp_expire_seconds: int = 300
    otp_max_attempts: int = 5

    rate_limit_start_per_minute: int = 8
    rate_limit_membership_per_minute: int = 12
    rate_limit_register_per_minute: int = 6
    rate_limit_referral_per_minute: int = 10
    rate_limit_login_per_minute: int = 8
    telegram_outbound_per_second: int = 25

    max_upload_mb: int = 5
    allowed_image_types: str = "image/jpeg,image/png,image/webp"

    room_credentials_retention_days: int = 7
    audit_log_retention_days: int = 365
    delivery_log_retention_days: int = 90
    soft_delete_purge_days: int = 180

    event_approval_required: bool = False
    auto_approve_organizers: bool = True
    max_events_per_organizer: int = 10
    max_required_channels_per_event: int = 5
    max_required_referrals: int = 20
    credentials_grace_minutes: int = 5
    custom_fill_minutes: int = 20
    event_retention_hours: int = 24
    past_events_hours: int = 24
    maintenance_mode: bool = False
    openapi_enabled: bool = True
    job_dispatch_interval_seconds: int = 60
    bot_embedded: bool = True

    sentry_dsn: str = ""
    prometheus_enabled: bool = False

    @field_validator("bot_username")
    @classmethod
    def strip_at(cls, v: str) -> str:
        return v.lstrip("@")

    @property
    def origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def image_types(self) -> set[str]:
        return {t.strip() for t in self.allowed_image_types.split(",") if t.strip()}

    @property
    def fernet_key(self) -> str:
        return self.room_credentials_key

    @property
    def backup_key(self) -> str:
        return self.backup_encryption_key or self.room_credentials_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
