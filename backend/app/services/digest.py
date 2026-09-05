from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import EventStatus, EventVisibility
from app.services.event_display import (
    event_public_load_options,
    format_event_list_label,
    organizer_public_name,
    resolve_event_channel,
    channel_public_label,
    event_about_text,
    event_prize_text,
)
from app.core.time import format_local
from app.models.event import Event

DIGEST_LIMIT = 8


def upcoming_prize_customs_sync(db: Session, *, limit: int = DIGEST_LIMIT) -> list[Event]:
    now = datetime.now(UTC)
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.deleted_at.is_(None),
                Event.visibility == EventVisibility.PUBLIC,
                Event.status.in_([EventStatus.PUBLISHED, EventStatus.FULL]),
                # still open: the organizer has not tapped "custom started"
                Event.archived_at.is_(None),
                Event.starts_at >= now - timedelta(minutes=get_settings().auto_archive_minutes),
                Event.deep_link_active.is_(True),
            )
            .options(*event_public_load_options())
            .order_by(Event.starts_at.asc())
            .limit(limit)
        ).all()
    )


def format_daily_digest(events: list[Event]) -> str:
    lines = [
        "🔥 <b>کاستوم‌های جایزه‌دار پیش‌رو</b>",
        "",
        "یکی را باز کنید، کانال‌های جوین اجباری را عضو شوید و دکمه سبز «عضو شدم» را بزنید.",
        "سر ساعت، ROOM ID و PASS فقط برای کسانی می‌آید که شرایط را انجام داده باشند.",
        "",
    ]
    for i, event in enumerate(events, start=1):
        when = format_local(event.starts_at, event.timezone)
        org = html.escape(organizer_public_name(event.organizer))
        ch = html.escape(channel_public_label(resolve_event_channel(event)))
        about = html.escape(event_about_text(event))
        prize = html.escape(event_prize_text(event))
        lines.append(f"{i}) 👤 <b>{org}</b> · 📢 {ch}")
        lines.append(f"📝 {about}")
        lines.append(f"💎 <b>جایزه:</b> {prize}")
        lines.append(f"🕐 {when}")
        lines.append("")
    lines.append("از دکمه‌های رنگی زیر وارد همان کاستوم شوید.")
    return "\n".join(lines).strip()


def digest_button_items(events: list[Event]) -> list[tuple[str, str]]:
    return [(event.public_token, format_event_list_label(event)) for event in events]
