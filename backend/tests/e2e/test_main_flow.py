"""E2E-style happy path using services (no live Telegram)."""


from tests.conftest import make_event, make_organizer, make_user
from app.core.security import encrypt_secret
from app.models.event import RoomCredential
from app.models.registration import Registration
from app.services.registration import try_confirm_with_lock_sync
from app.services.scheduler import cancel_event_jobs_sync, schedule_event_jobs_sync
from app.core.enums import EventStatus
from sqlalchemy import func, select


def test_main_flow(db):
    admin = make_user(db, 1)
    organizer_user = make_user(db, 2)
    player = make_user(db, 3)
    org = make_organizer(db, organizer_user)
    event = make_event(db, org, capacity=1, status="pending_approval")
    event.status = EventStatus.PUBLISHED
    db.add(
        RoomCredential(
            event_id=event.id,
            room_id_encrypted=encrypt_secret("9999"),
            room_password_encrypted=encrypt_secret("pass"),
        )
    )
    schedule_event_jobs_sync(db, event)
    result = try_confirm_with_lock_sync(db, player.id, event.id)
    assert result == "confirmed"
    extra = make_user(db, 4)
    assert try_confirm_with_lock_sync(db, extra.id, event.id) == "waitlisted"
    event.status = EventStatus.CANCELLED
    n = cancel_event_jobs_sync(db, event.id)
    assert n >= 1
    confirmed = db.scalar(
        select(func.count()).select_from(Registration).where(Registration.status == "confirmed", Registration.event_id == event.id)
    )
    assert confirmed == 1
