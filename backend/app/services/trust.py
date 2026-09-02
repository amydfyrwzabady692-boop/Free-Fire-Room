"""Organizer trust score.

The ``organizers.trust_score`` column and the ``organizer_trust_events`` table
already existed but nothing ever wrote to them. This module is the single place
that moves the score, so every change leaves an auditable row behind saying why.

The score is the only signal a player has about whether an organizer actually
pays out, which is the one thing this bot cannot enforce technically.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.organizer import Organizer, OrganizerTrustEvent

START_SCORE = 50.0
MIN_SCORE = 0.0
MAX_SCORE = 100.0


@dataclass(frozen=True)
class TrustRule:
    event_type: str
    delta: float
    reason: str
    #: at most this many times per organizer per related event
    once_per_event: bool = True


RULES: dict[str, TrustRule] = {
    "credentials_delivered": TrustRule(
        "credentials_delivered", +6.0, "ROOM ID و PASS سر وقت برای بازیکن‌ها ارسال شد"
    ),
    "credentials_missed": TrustRule(
        "credentials_missed", -18.0, "در مهلت مقرر ROOM ID و PASS را نفرستاد"
    ),
    "event_cancelled_late": TrustRule(
        "event_cancelled_late", -8.0, "کاستوم را نزدیک ساعت شروع لغو کرد"
    ),
    "prize_paid_confirmed": TrustRule(
        "prize_paid_confirmed", +4.0, "بازیکن‌ها تأیید کردند جایزه پرداخت شد"
    ),
    "prize_unpaid_reported": TrustRule(
        "prize_unpaid_reported", -14.0, "گزارش تأییدشده: جایزه پرداخت نشد"
    ),
    "report_upheld": TrustRule("report_upheld", -10.0, "گزارش تخلف توسط مدیر تأیید شد"),
    "good_review": TrustRule("good_review", +2.0, "امتیاز بالای بازیکن‌ها", once_per_event=False),
    "bad_review": TrustRule("bad_review", -3.0, "امتیاز پایین بازیکن‌ها", once_per_event=False),
    "admin_adjustment": TrustRule("admin_adjustment", 0.0, "تنظیم دستی مدیر", once_per_event=False),
}


def _clamp(value: float) -> float:
    return max(MIN_SCORE, min(MAX_SCORE, round(value, 2)))


def _recompute(organizer: Organizer, delta: float) -> float:
    base = organizer.trust_score if organizer.trust_score is not None else START_SCORE
    organizer.trust_score = _clamp(float(base) + delta)
    return organizer.trust_score


async def record(
    db: AsyncSession,
    organizer: Organizer,
    event_type: str,
    *,
    related_event_id=None,
    actor_id=None,
    delta: float | None = None,
    reason: str | None = None,
) -> float | None:
    """Apply a trust rule. Returns the new score, or None if it was a no-op."""
    rule = RULES.get(event_type)
    if rule is None:
        return None
    if rule.once_per_event and related_event_id is not None:
        seen = await db.scalar(
            select(func.count())
            .select_from(OrganizerTrustEvent)
            .where(
                OrganizerTrustEvent.organizer_id == organizer.id,
                OrganizerTrustEvent.event_type == event_type,
                OrganizerTrustEvent.related_event_id == related_event_id,
            )
        )
        if seen:
            return None
    applied = rule.delta if delta is None else float(delta)
    db.add(
        OrganizerTrustEvent(
            organizer_id=organizer.id,
            event_type=event_type,
            delta=applied,
            reason=reason or rule.reason,
            related_event_id=related_event_id,
            created_by=actor_id,
        )
    )
    score = _recompute(organizer, applied)
    await db.flush()
    return score


def record_sync(
    db: Session,
    organizer: Organizer,
    event_type: str,
    *,
    related_event_id=None,
    actor_id=None,
    delta: float | None = None,
    reason: str | None = None,
) -> float | None:
    """Same as :func:`record`, for the sync sessions the workers use."""
    rule = RULES.get(event_type)
    if rule is None:
        return None
    if rule.once_per_event and related_event_id is not None:
        seen = db.scalar(
            select(func.count())
            .select_from(OrganizerTrustEvent)
            .where(
                OrganizerTrustEvent.organizer_id == organizer.id,
                OrganizerTrustEvent.event_type == event_type,
                OrganizerTrustEvent.related_event_id == related_event_id,
            )
        )
        if seen:
            return None
    applied = rule.delta if delta is None else float(delta)
    db.add(
        OrganizerTrustEvent(
            organizer_id=organizer.id,
            event_type=event_type,
            delta=applied,
            reason=reason or rule.reason,
            related_event_id=related_event_id,
            created_by=actor_id,
        )
    )
    score = _recompute(organizer, applied)
    db.flush()
    return score


def badge(score: float | None) -> str:
    """Short Persian label for a score, safe to put in a button or a card."""
    value = START_SCORE if score is None else float(score)
    if value >= 85:
        return "🟢 بسیار مطمئن"
    if value >= 70:
        return "🟢 مطمئن"
    if value >= 50:
        return "🔵 معمولی"
    if value >= 30:
        return "🟠 کم‌اعتبار"
    return "🔴 پرریسک"


def format_trust_line(organizer: Organizer | None, *, prefix: str = "اعتبار برگزارکننده") -> str:
    if organizer is None:
        return ""
    score = organizer.trust_score if organizer.trust_score is not None else START_SCORE
    return f"{prefix}: {badge(score)} ({int(round(float(score)))}/100)"


def is_risky(organizer: Organizer | None, threshold: float = 30.0) -> bool:
    if organizer is None:
        return False
    score = organizer.trust_score if organizer.trust_score is not None else START_SCORE
    return float(score) < threshold


async def history(db: AsyncSession, organizer_id, limit: int = 10) -> list[OrganizerTrustEvent]:
    rows = await db.scalars(
        select(OrganizerTrustEvent)
        .where(OrganizerTrustEvent.organizer_id == organizer_id)
        .order_by(OrganizerTrustEvent.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())
