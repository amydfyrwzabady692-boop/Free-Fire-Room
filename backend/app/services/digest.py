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
        "🔥 <b>کاستوم‌های جایزه‌دار پیش‌رو</b>",
        "",
        "یکی را باز کنید، کانال‌های جوین اجباری را عضو شوید و دکمه سبز «عضو شدم» را بزنید.",
        "سر ساعت، آیدی و رمز فقط برای کسانی می‌آید که شرایط را انجام داده باشند.",
        "",
    ]
    for i, event in enumerate(events, start=1):
        when = format_local(event.starts_at, event.timezone)
        org = html.escape(
            event.organizer.display_name if event.organizer and event.organizer.display_name else "برگزارکننده"
        )
        prize = html.escape((event.prize_summary or event.title or "کاستوم").strip())
        lines.append(f"{i}) 🎁 <b>{prize}</b>")
        lines.append(f"🕐 {when}")
        lines.append(f"👤 {org}")
        lines.append("")
    lines.append("از دکمه‌های رنگی زیر وارد همان کاستوم شوید.")
    return "\n".join(lines).strip()


def digest_button_items(events: list[Event]) -> list[tuple[str, str]]:
    items = []
    for event in events:
        stamp = format_local(event.starts_at, event.timezone, compact=True)
        prize = (event.prize_summary or "").strip().replace("\n", " ")
        label = prize[:36] if prize else event.title
        items.append((event.public_token, f"🕐 {stamp} · 🎁 {label}"))
    return items
