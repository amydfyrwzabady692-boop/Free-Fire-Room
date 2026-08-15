from __future__ import annotations

import html
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import EventStatus, EventVisibility
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
                Event.starts_at >= now,
                Event.deep_link_active.is_(True),
            )
            .options(selectinload(Event.organizer))
            .order_by(Event.starts_at.asc())
            .limit(limit)
        ).all()
    )


def format_daily_digest(events: list[Event]) -> str:
    lines = [
        "کاستوم‌های جایزه‌دار پیش‌رو",
        "",
        "اگر می‌خواهید شرکت کنید، یکی را باز کنید، کانال‌های جوین اجباری را عضو شوید و «عضو شدم» را بزنید.",
        "سر ساعت همان کاستوم، آیدی و رمز فقط برای کسانی می‌آید که شرایط را انجام داده باشند.",
        "",
    ]
    for i, event in enumerate(events, start=1):
        when = format_local(event.starts_at, event.timezone)
        org = html.escape(event.organizer.display_name) if event.organizer and event.organizer.display_name else "برگزارکننده"
        lines.append(f"{i}) {when}")
        lines.append(html.escape(event.title))
        lines.append(f"برگزارکننده: {org}")
        lines.append("")
    lines.append("دکمه‌های زیر را بزنید تا وارد همان کاستوم شوید.")
    return "\n".join(lines).strip()


def digest_button_items(events: list[Event]) -> list[tuple[str, str]]:
    items = []
    for event in events:
        stamp = format_local(event.starts_at, event.timezone, compact=True)
        items.append((event.public_token, f"{stamp} | {event.title}"))
    return items
