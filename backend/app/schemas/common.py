from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    #: minted on every login but there was no endpoint to spend it, so the
    #: panel simply died after access_token_expire_minutes
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None


class RefreshIn(BaseModel):
    refresh_token: str


class OtpRequestIn(BaseModel):
    telegram_id: int


class LoginPasswordIn(BaseModel):
    telegram_id: int
    password: str
    totp_code: str | None = None


class LoginOtpIn(BaseModel):
    telegram_id: int
    code: str


class TelegramLoginIn(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str


class EventCreateIn(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str | None = None
    banner_file_id: str | None = None
    starts_at: datetime
    registration_ends_at: datetime
    credentials_send_at: datetime
    timezone: str = "Asia/Tehran"
    region: str = "ME"
    game_mode: str = "squad"
    #: 0 = unlimited, which is the default for customs made in the bot
    capacity: int = Field(default=0, ge=0, le=10000)
    waitlist_enabled: bool = True
    visibility: str = "public"
    require_rules_accept: bool = True
    require_ff_player_id: bool = False
    require_profile_complete: bool = False
    required_referrals: int = Field(default=0, ge=0, le=50)
    rules_text: str | None = None
    winner_method: str | None = None
    custom_credentials_message: str | None = None
    reveal_button_enabled: bool = True
    personalize_delivery: bool = True
    #: notify everyone 1 hour and 10 minutes before a custom starts
    reminder_offsets_minutes: list[int] = Field(default_factory=lambda: [60, 10])
    prize_summary: str | None = None
    payout_contact: str | None = Field(default=None, max_length=128)
    social_url: str | None = Field(default=None, max_length=300)
    social_platform: str | None = Field(default=None, max_length=32)
    social_note: str | None = Field(default=None, max_length=500)
    prizes: list[dict[str, Any]] = Field(default_factory=list)
    required_channel_ids: list[UUID] = Field(default_factory=list)
    channel_id: UUID | None = None
    room_id: str | None = Field(default=None, max_length=32)
    room_password: str | None = Field(default=None, max_length=32)


class EventFilter(BaseModel):
    when: str | None = None
    free: bool | None = None
    has_capacity: bool | None = None
    game_mode: str | None = None
    region: str | None = None
    verified_only: bool | None = None
    sort: str = "soonest"


class BanIn(BaseModel):
    scope: str
    reason: str = Field(min_length=3, max_length=2000)
    expires_at: datetime | None = None


class ReportIn(BaseModel):
    event_id: UUID | None = None
    organizer_id: UUID | None = None
    reason: str
    body: str = Field(min_length=5, max_length=4000)


class BroadcastIn(BaseModel):
    title: str
    body: str
    media_type: str | None = None
    media_file_id: str | None = None
    buttons: list[dict[str, str]] | None = None
    targeting: dict[str, Any] | None = None
    scheduled_at: datetime | None = None


class ChannelIn(BaseModel):
    chat_ref: str
    scope: str = "all"
    sort_order: int = 0


class SettingIn(BaseModel):
    key: str
    value: Any
    description: str | None = None


class RescheduleIn(BaseModel):
    starts_at: datetime
    registration_ends_at: datetime
    credentials_send_at: datetime


class CredentialsIn(BaseModel):
    room_id: str = Field(min_length=1, max_length=32)
    room_password: str = Field(min_length=1, max_length=32)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ProfileIn(BaseModel):
    ff_player_id: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=32)
    preferred_mode: str | None = None
    timezone: str | None = None
    language: str | None = None
