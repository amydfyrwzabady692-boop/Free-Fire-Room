"""Tests for the two things the schema always supported but nothing exposed:
the organizer trust score and the per-custom funnel."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.enums import DeliveryStatus, RegistrationStatus
from app.models.analytics import EventView
from app.models.jobs import Delivery
from app.models.organizer import OrganizerTrustEvent
from app.models.registration import Registration
from app.services import trust
from app.services.funnel import biggest_drop, event_funnel, format_funnel, record_view
from tests.conftest import make_event, make_organizer, make_user


# --------------------------------------------------------------- trust score


def test_trust_starts_neutral_and_moves_with_rules(db):
    org = make_organizer(db, make_user(db, 1001))
    assert org.trust_score == 50.0

    score = trust.record_sync(db, org, "credentials_delivered", related_event_id=None)
    assert score == 56.0
    score = trust.record_sync(db, org, "credentials_missed", related_event_id=None)
    assert score == 38.0

    rows = db.scalars(select(OrganizerTrustEvent).where(OrganizerTrustEvent.organizer_id == org.id)).all()
    assert len(rows) == 2, "every move must leave an auditable row"
    assert all(r.reason for r in rows)


def test_trust_rule_applies_once_per_event(db):
    host = make_user(db, 1002)
    org = make_organizer(db, host)
    event = make_event(db, org)
    db.flush()

    first = trust.record_sync(db, org, "credentials_delivered", related_event_id=event.id)
    second = trust.record_sync(db, org, "credentials_delivered", related_event_id=event.id)

    assert first == 56.0
    assert second is None, "the delivery job re-runs during the fill window; it must not stack"
    assert org.trust_score == 56.0


def test_trust_is_clamped_to_the_0_100_range(db):
    org = make_organizer(db, make_user(db, 1003))
    for i in range(20):
        trust.record_sync(db, org, "credentials_missed", related_event_id=None)
    assert org.trust_score == 0.0
    for i in range(40):
        trust.record_sync(db, org, "credentials_delivered", related_event_id=None)
    assert org.trust_score == 100.0


def test_trust_badge_and_risk_flag(db):
    org = make_organizer(db, make_user(db, 1004))
    org.trust_score = 92.0
    assert "بسیار مطمئن" in trust.badge(org.trust_score)
    assert not trust.is_risky(org)
    org.trust_score = 12.0
    assert "پرریسک" in trust.badge(org.trust_score)
    assert trust.is_risky(org)
    assert "12/100" in trust.format_trust_line(org)


def test_unknown_trust_rule_is_a_no_op(db):
    org = make_organizer(db, make_user(db, 1005))
    assert trust.record_sync(db, org, "not_a_real_rule") is None
    assert org.trust_score == 50.0


# --------------------------------------------------------------------- funnel


@pytest.mark.asyncio
async def test_record_view_is_idempotent(async_db):
    db = async_db
    host = await db.run_sync(lambda s: make_user(s, 1101))
    org = await db.run_sync(lambda s: make_organizer(s, host))
    event = await db.run_sync(lambda s: make_event(s, org))
    player = await db.run_sync(lambda s: make_user(s, 1102))
    await db.commit()

    await record_view(db, event.id, player.id, source="deep_link")
    await record_view(db, event.id, player.id, source="list")
    await db.commit()

    count = len((await db.scalars(select(EventView).where(EventView.event_id == event.id))).all())
    assert count == 1


@pytest.mark.asyncio
async def test_funnel_counts_each_stage(async_db):
    db = async_db
    host = await db.run_sync(lambda s: make_user(s, 1103))
    org = await db.run_sync(lambda s: make_organizer(s, host))
    event = await db.run_sync(lambda s: make_event(s, org, capacity=10))
    viewers = []
    for i in range(6):
        viewers.append(await db.run_sync(lambda s, i=i: make_user(s, 1200 + i)))
    await db.commit()

    for u in viewers:
        await record_view(db, event.id, u.id, source="deep_link")
    # four of the six actually started, three of those qualified
    for u in viewers[:4]:
        db.add(
            Registration(
                event_id=event.id,
                user_id=u.id,
                status=RegistrationStatus.CONFIRMED if u in viewers[:3] else RegistrationStatus.PENDING,
                source="deep_link",
            )
        )
    await db.flush()
    # two of the three confirmed actually received the room
    for i, u in enumerate(viewers[:2]):
        db.add(
            Delivery(
                user_id=u.id,
                event_id=event.id,
                kind="room_credentials",
                status=DeliveryStatus.SENT,
                idempotency_key=f"creds:{event.id}:{u.id}:1",
                sent_at=datetime.now(UTC),
            )
        )
    await db.commit()

    stats = await event_funnel(db, event.id)
    assert stats["viewed"] == 6
    assert stats["started"] == 4
    assert stats["confirmed"] == 3
    assert stats["pending"] == 1
    assert stats["delivered"] == 2
    assert stats["from_link"] == 4

    text = format_funnel(stats)
    assert "قیف این کاستوم" in text
    for value in ("6", "4", "3", "2"):
        assert value in text


def test_biggest_drop_points_at_the_worst_stage():
    assert biggest_drop({"viewed": 40, "started": 5, "confirmed": 5, "delivered": 5}).startswith("بیشتر")
    assert "کانال‌های اجباری" in biggest_drop(
        {"viewed": 20, "started": 20, "confirmed": 4, "delivered": 4}
    )
    assert "از کانال خارج" in biggest_drop(
        {"viewed": 20, "started": 20, "confirmed": 20, "delivered": 2}
    )
    # a healthy funnel has nothing to complain about
    assert biggest_drop({"viewed": 10, "started": 10, "confirmed": 9, "delivered": 9}) is None


# ------------------------------------------------- the worker wiring itself


def test_missed_credentials_costs_trust_and_is_not_double_counted(db):
    """_expire_missing_credentials runs once per event, but be sure that even
    if the job is retried the organizer is only penalised once."""
    host = make_user(db, 1301)
    org = make_organizer(db, host)
    event = make_event(db, org)
    db.commit()
    before = org.trust_score

    trust.record_sync(db, org, "credentials_missed", related_event_id=event.id)
    after_first = org.trust_score
    trust.record_sync(db, org, "credentials_missed", related_event_id=event.id)

    assert after_first == before - 18.0
    assert org.trust_score == after_first, "a retried job must not penalise twice"


def test_delivered_and_missed_pull_in_opposite_directions(db):
    good = make_organizer(db, make_user(db, 1302))
    bad = make_organizer(db, make_user(db, 1303))
    db.commit()

    trust.record_sync(db, good, "credentials_delivered")
    trust.record_sync(db, bad, "credentials_missed")

    assert good.trust_score > 50 > bad.trust_score
    assert not trust.is_risky(good)
    assert trust.is_risky(bad, threshold=40)
