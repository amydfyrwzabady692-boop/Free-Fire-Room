from datetime import UTC, datetime, timedelta

from app.core.security import decrypt_secret, encrypt_secret, generate_unguessable_token
from app.services.registration import try_confirm_with_lock_sync
from app.services.referrals import apply_referral_sync
from app.services.scheduler import cancel_event_jobs_sync, schedule_event_jobs_sync
from app.models.referral import ReferralLink
from app.models.registration import Registration
from app.models.user import Ban
from sqlalchemy import select, func


def test_encryption_roundtrip():
    token = encrypt_secret("room-secret-99")
    assert "room-secret" not in token
    assert decrypt_secret(token) == "room-secret-99"


def test_token_not_sequential():
    a = generate_unguessable_token(18)
    b = generate_unguessable_token(18)
    assert a != b
    assert len(a) > 16


def test_capacity_lock(db, make_helpers=None):
    from tests.conftest import make_event, make_organizer, make_user

    host = make_user(db, 1)
    org = make_organizer(db, host)
    event = make_event(db, org, capacity=2)
    results = []
    for i in range(5):
        u = make_user(db, 100 + i)
        results.append(try_confirm_with_lock_sync(db, u.id, event.id))
    db.commit()
    confirmed = db.scalar(
        select(func.count()).select_from(Registration).where(Registration.event_id == event.id, Registration.status == "confirmed")
    )
    assert confirmed == 2
    assert results.count("confirmed") == 2
    assert results.count("waitlisted") == 3


def test_referral_self_and_duplicate(db):
    from tests.conftest import make_event, make_organizer, make_user

    a = make_user(db, 10)
    b = make_user(db, 11)
    org = make_organizer(db, a)
    event = make_event(db, org)
    link = ReferralLink(user_id=a.id, event_id=event.id, token="tokA", campaign="default")
    db.add(link)
    db.flush()
    assert apply_referral_sync(db, a.id, a.id, event.id, "tokA") == "self"
    assert apply_referral_sync(db, a.id, b.id, event.id, "tokA") == "valid"
    assert apply_referral_sync(db, a.id, b.id, event.id, "tokA") == "duplicate"
    db.refresh(link)
    assert link.valid_count == 1


def test_ban_blocks_scope(db):
    from tests.conftest import make_user
    from app.services.bans import is_banned_sync
    from app.core.enums import BanScope

    u = make_user(db, 22)
    db.add(Ban(user_id=u.id, scope="participate", reason="abuse", is_active=True))
    db.flush()
    assert is_banned_sync(db, u, BanScope.PARTICIPATE)
    assert not is_banned_sync(db, u, BanScope.BOT)


def test_cancel_disables_jobs(db):
    from tests.conftest import make_event, make_organizer, make_user
    from app.models.jobs import ScheduledJob

    host = make_user(db, 30)
    org = make_organizer(db, host)
    event = make_event(db, org)
    schedule_event_jobs_sync(db, event)
    n = cancel_event_jobs_sync(db, event.id)
    assert n >= 1
    rows = db.scalars(select(ScheduledJob).where(ScheduledJob.entity_id == event.id)).all()
    assert all(r.status == "cancelled" for r in rows)


def test_reschedule_updates_run_at(db):
    from tests.conftest import make_event, make_organizer, make_user
    from app.models.jobs import ScheduledJob
    from app.core.enums import JobType
    from app.core.time import as_utc

    host = make_user(db, 31)
    org = make_organizer(db, host)
    event = make_event(db, org)
    schedule_event_jobs_sync(db, event)
    event.credentials_send_at = event.credentials_send_at + timedelta(hours=1)
    event.starts_at = event.starts_at + timedelta(hours=1)
    schedule_event_jobs_sync(db, event)
    job = db.scalar(
        select(ScheduledJob).where(ScheduledJob.entity_id == event.id, ScheduledJob.job_type == JobType.SEND_CREDENTIALS)
    )
    # SQLite drops tzinfo on the round-trip; compare the instants, not the reprs
    assert as_utc(job.run_at) == as_utc(event.credentials_send_at)


def test_credentials_idempotent_delivery_row(db):
    from tests.conftest import make_event, make_organizer, make_user
    from app.core.security import encrypt_secret
    from app.models.event import RoomCredential
    from app.models.jobs import Delivery, ScheduledJob
    from app.services.credentials import _upsert_delivery
    from app.core.enums import DeliveryStatus, JobType, JobStatus

    host = make_user(db, 40)
    player = make_user(db, 41)
    org = make_organizer(db, host)
    event = make_event(db, org)
    creds = RoomCredential(
        event_id=event.id,
        room_id_encrypted=encrypt_secret("111"),
        room_password_encrypted=encrypt_secret("pwd"),
    )
    db.add(creds)
    job = ScheduledJob(
        job_type=JobType.SEND_CREDENTIALS,
        entity_type="event",
        entity_id=event.id,
        run_at=datetime.now(UTC),
        status=JobStatus.RUNNING,
        idempotency_key=f"send:{event.id}",
    )
    db.add(job)
    db.flush()
    idem = f"creds:{event.id}:{player.id}:1"
    _upsert_delivery(db, user=player, event=event, job=job, idem=idem, status=DeliveryStatus.SENT, telegram_message_id=1)
    _upsert_delivery(db, user=player, event=event, job=job, idem=idem, status=DeliveryStatus.SENT, telegram_message_id=2)
    count = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.idempotency_key == idem))
    assert count == 1


def test_job_claim_not_double(db):
    from uuid import uuid4

    from app.models.jobs import ScheduledJob
    from app.core.enums import JobType, JobStatus
    from app.services.scheduler import claim_due_jobs_sync

    job = ScheduledJob(
        job_type=JobType.SEND_CREDENTIALS,
        entity_type="event",
        entity_id=uuid4(),
        run_at=datetime.now(UTC) - timedelta(seconds=1),
        status=JobStatus.PENDING,
        idempotency_key="only-once",
    )
    db.add(job)
    db.flush()
    first = claim_due_jobs_sync(db, "w1")
    second = claim_due_jobs_sync(db, "w2")
    assert len(first) == 1
    assert len(second) == 0
    assert first[0].status == JobStatus.RUNNING


def test_organizer_isolation_query(db):
    from tests.conftest import make_event, make_organizer, make_user
    from app.models.event import Event

    a = make_user(db, 50)
    b = make_user(db, 51)
    oa = make_organizer(db, a)
    ob = make_organizer(db, b)
    ea = make_event(db, oa, title="A")
    eb = make_event(db, ob, title="B")
    mine = db.scalars(select(Event).where(Event.organizer_id == oa.id)).all()
    assert {e.id for e in mine} == {ea.id}
    assert eb.id not in {e.id for e in mine}


def test_requirement_capacity_helper():
    from types import SimpleNamespace
    from app.services.requirements import evaluate_capacity_only

    event = SimpleNamespace(confirmed_count=10, capacity=10)
    assert evaluate_capacity_only(event, already_confirmed=False) is False
    assert evaluate_capacity_only(event, already_confirmed=True) is True


def test_credentials_message_uses_room_id_and_pass():
    from types import SimpleNamespace

    from app.services.credentials import _render_credentials_message

    event = SimpleNamespace(
        title="کاستوم الماس",
        prize_summary="۱۰۰۰ الماس",
        custom_credentials_message=None,
        personalize_delivery=False,
    )
    user = SimpleNamespace(first_name="Ali", username="ali", telegram_id=1, id="abcd1234")
    text = _render_credentials_message(event, user, "12345678", "pass99", 1)
    assert "ROOM ID" in text
    assert "PASS" in text
    assert "12345678" in text
    assert "pass99" in text
    assert "آیدی اتاق" not in text
    assert "رمز اتاق" not in text
    assert "اتاق کاستوم" not in text
    assert "کاستوم آماده است" in text


def test_status_labels_are_persian():
    from app.core.enums import EventStatus, RegistrationStatus
    from app.locales.labels import event_status_fa, reg_status_fa

    assert event_status_fa(EventStatus.CANCELLED) == "لغو شده"
    assert event_status_fa("published") == "منتشرشده"
    assert reg_status_fa(RegistrationStatus.CONFIRMED) == "ثبت‌نام قطعی"
    assert "confirmed" not in reg_status_fa(RegistrationStatus.CONFIRMED)
