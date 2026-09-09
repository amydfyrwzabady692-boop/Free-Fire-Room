"""The optional follow-the-page gate the organizer can add to a custom."""

from datetime import UTC, datetime

import pytest

from app.core.enums import (
    RegistrationStatus,
    RequirementStatus,
    RequirementType,
    SocialPlatform,
    SocialProofStatus,
)
from app.core.errors import AppError
from app.models.organizer import Organizer
from app.models.social import EventSocialTask, SocialProof
from app.models.user import User, UserProfile
from app.services.registration import register_user
from app.services.requirements import evaluate_requirements
from app.services.social import (
    detect_platform,
    normalize_social_url,
    social_gate_ok_sync,
    social_required,
    submit_proof,
)
from tests.conftest import make_event, make_organizer, make_user


def test_url_normalisation_accepts_link_handle_and_bare_domain():
    assert normalize_social_url("https://instagram.com/x") == ("https://instagram.com/x", "instagram")
    assert normalize_social_url("@mypage") == ("https://instagram.com/mypage", "instagram")
    assert normalize_social_url("youtube.com/@ch")[1] == SocialPlatform.YOUTUBE
    assert normalize_social_url("https://youtu.be/abc")[1] == SocialPlatform.YOUTUBE
    assert detect_platform("https://example.com/p") == SocialPlatform.OTHER


def test_url_normalisation_rejects_junk():
    for bad in ("", "   ", "just some words", "@"):
        with pytest.raises(AppError):
            normalize_social_url(bad)


def test_social_not_required_by_default(db):
    host = make_user(db, 9001)
    org = make_organizer(db, host)
    event = make_event(db, org)
    assert social_required(event) is False
    player = make_user(db, 9002)
    assert social_gate_ok_sync(db, event, player) is True


def test_sending_the_screenshot_opens_the_gate_and_a_rejection_closes_it(db):
    """Sending is the requirement. Only a rejection takes the room back."""
    host = make_user(db, 9010)
    org = make_organizer(db, host)
    event = make_event(db, org)
    event.social_url = "https://instagram.com/page"
    event.social_platform = SocialPlatform.INSTAGRAM
    db.flush()
    from app.services.social import seed_tasks

    seed_tasks(db, event, [{"url": event.social_url, "platform": SocialPlatform.INSTAGRAM}])
    db.flush()
    task = db.scalars(
        __import__("sqlalchemy").select(EventSocialTask).where(
            EventSocialTask.event_id == event.id
        )
    ).one()
    player = make_user(db, 9011)

    assert social_gate_ok_sync(db, event, player) is False

    proof = SocialProof(
        event_id=event.id,
        user_id=player.id,
        task_id=task.id,
        file_id="f1",
        status=SocialProofStatus.PENDING,
    )
    db.add(proof)
    db.flush()
    assert social_gate_ok_sync(db, event, player) is True, "sending it is enough"

    proof.status = SocialProofStatus.REJECTED
    db.flush()
    assert social_gate_ok_sync(db, event, player) is False, "a rejection voids the entry"

    proof.status = SocialProofStatus.APPROVED
    db.flush()
    assert social_gate_ok_sync(db, event, player) is True


async def _seed_async(async_db, *, social: bool):
    from app.core.security import generate_unguessable_token
    from app.models.event import Event

    host = User(telegram_id=9100, first_name="host")
    player = User(telegram_id=9101, first_name="player")
    async_db.add_all([host, player])
    await async_db.flush()
    async_db.add_all([UserProfile(user_id=host.id), UserProfile(user_id=player.id)])
    org = Organizer(user_id=host.id, status="approved")
    async_db.add(org)
    await async_db.flush()
    now = datetime.now(UTC)
    event = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="Custom",
        starts_at=now,
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status="published",
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        social_url="https://instagram.com/page" if social else None,
        social_platform=SocialPlatform.INSTAGRAM if social else None,
    )
    async_db.add(event)
    await async_db.flush()
    if social:
        from app.services.social import seed_tasks

        seed_tasks(async_db, event, [{"url": event.social_url, "platform": SocialPlatform.INSTAGRAM}])
        await async_db.flush()
    return event, player


async def test_social_requirement_is_the_last_item(async_db):
    event, player = await _seed_async(async_db, social=True)
    checklist = await evaluate_requirements(async_db, user=player, event=event, bot=None)
    assert checklist.items[-1].requirement_type == RequirementType.SOCIAL_FOLLOW
    assert checklist.items[-1].status == RequirementStatus.NOT_DONE


async def test_the_screenshot_confirms_the_registration_with_nobody_approving(async_db):
    event, player = await _seed_async(async_db, social=True)

    result = await register_user(async_db, user=player, event=event, bot=None, accept_rules=True)
    assert result.registration.status == RegistrationStatus.PENDING
    assert event.confirmed_count == 0

    from app.services.social import list_tasks

    task = (await list_tasks(async_db, event.id))[0]
    await submit_proof(async_db, event=event, user=player, file_id="shot", task=task)

    result = await register_user(async_db, user=player, event=event, bot=None, accept_rules=True)
    assert result.registration.status == RegistrationStatus.CONFIRMED
    assert event.confirmed_count == 1


async def test_a_rejection_takes_the_registration_back(async_db):
    event, player = await _seed_async(async_db, social=True)
    from app.services.social import get_proof, list_tasks, review_proof

    task = (await list_tasks(async_db, event.id))[0]
    await submit_proof(async_db, event=event, user=player, file_id="shot", task=task)
    result = await register_user(async_db, user=player, event=event, bot=None, accept_rules=True)
    assert result.registration.status == RegistrationStatus.CONFIRMED

    row = await get_proof(async_db, event_id=event.id, user_id=player.id, task_id=task.id)
    await review_proof(async_db, row, approved=False, reviewer_id=player.id)

    checklist = await evaluate_requirements(async_db, user=player, event=event, bot=None)
    social = [i for i in checklist.items if i.requirement_type == RequirementType.SOCIAL_FOLLOW][0]
    assert social.status == RequirementStatus.NOT_DONE
    from app.services.social import social_gate_ok

    assert await social_gate_ok(async_db, event, player) is False


async def test_without_the_gate_a_player_confirms_straight_away(async_db):
    event, player = await _seed_async(async_db, social=False)
    result = await register_user(async_db, user=player, event=event, bot=None, accept_rules=True)
    assert result.registration.status == RegistrationStatus.CONFIRMED


async def _multi(async_db, urls):
    """A custom that asks for several pages, the way create_event builds one."""
    from app.core.security import generate_unguessable_token
    from app.models.event import Event
    from app.services.social import seed_tasks

    host = User(telegram_id=9200, first_name="host")
    player = User(telegram_id=9201, first_name="player")
    async_db.add_all([host, player])
    await async_db.flush()
    async_db.add_all([UserProfile(user_id=host.id), UserProfile(user_id=player.id)])
    org = Organizer(user_id=host.id, status="approved")
    async_db.add(org)
    await async_db.flush()
    now = datetime.now(UTC)
    event = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="Custom",
        starts_at=now,
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status="published",
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        social_url=urls[0],
        social_platform=SocialPlatform.INSTAGRAM,
    )
    async_db.add(event)
    await async_db.flush()
    seed_tasks(async_db, event, [{"url": u, "platform": None} for u in urls])
    await async_db.flush()
    return event, player


async def test_the_player_is_walked_through_every_page(async_db):
    from app.services.social import list_tasks, next_task_for, submit_proof

    urls = ["https://instagram.com/a", "https://youtube.com/@b", "https://instagram.com/c"]
    event, player = await _multi(async_db, urls)
    tasks = await list_tasks(async_db, event.id)
    assert [t.url for t in tasks] == urls
    # the platform is detected per page, not copied from the first one
    assert tasks[1].platform == SocialPlatform.YOUTUBE

    seen = []
    for _ in range(len(urls)):
        task, done, total = await next_task_for(async_db, event=event, user=player)
        assert total == 3
        assert done == len(seen)
        seen.append(task.url)
        await submit_proof(async_db, event=event, user=player, file_id="s", task=task)

    assert seen == urls
    task, done, total = await next_task_for(async_db, event=event, user=player)
    assert task is None
    assert done == 3


async def test_every_page_needs_a_screenshot_and_none_may_be_rejected(async_db):
    from app.services.social import get_proof, list_tasks, review_proof, social_gate_ok, submit_proof

    event, player = await _multi(async_db, ["https://instagram.com/a", "https://instagram.com/b"])
    tasks = await list_tasks(async_db, event.id)
    await submit_proof(async_db, event=event, user=player, file_id="s", task=tasks[0])
    # one page out of two is not enough
    assert await social_gate_ok(async_db, event, player) is False

    await submit_proof(async_db, event=event, user=player, file_id="s", task=tasks[1])
    assert await social_gate_ok(async_db, event, player) is True

    first = await get_proof(async_db, event_id=event.id, user_id=player.id, task_id=tasks[0].id)
    await review_proof(async_db, first, approved=False, reviewer_id=player.id)
    assert await social_gate_ok(async_db, event, player) is False, "one bad page closes the gate"

    await submit_proof(async_db, event=event, user=player, file_id="better", task=tasks[0])
    assert await social_gate_ok(async_db, event, player) is True, "resending reopens it"


async def test_a_rejected_page_comes_back_around(async_db):
    from app.services.social import get_proof, list_tasks, next_task_for, review_proof, submit_proof

    event, player = await _multi(async_db, ["https://instagram.com/a", "https://instagram.com/b"])
    tasks = await list_tasks(async_db, event.id)
    for task in tasks:
        await submit_proof(async_db, event=event, user=player, file_id="s", task=task)
    first = await get_proof(async_db, event_id=event.id, user_id=player.id, task_id=tasks[0].id)
    await review_proof(async_db, first, approved=False, reviewer_id=player.id)

    task, done, total = await next_task_for(async_db, event=event, user=player)
    assert task is not None
    assert task.id == tasks[0].id, "the rejected page should be asked for again"
    assert (done, total) == (1, 2)


def test_the_step_text_lists_every_page(db):
    from app.services.social import format_social_step

    host = make_user(db, 9300)
    org = make_organizer(db, host)
    event = make_event(db, org)
    tasks = [
        EventSocialTask(event_id=event.id, url="https://instagram.com/a", platform="instagram", sort_order=0),
        EventSocialTask(event_id=event.id, url="https://youtube.com/@b", platform="youtube", sort_order=1),
    ]
    text = format_social_step(event, tasks, done=1)
    assert "instagram.com/a" in text
    assert "youtube.com/@b" in text
    assert "۱ از ۲" in text
    assert "برای هر پیج یک اسکرین جدا لازم است" in text
    # the player is told what to do, never how the bot handles it afterwards
    assert "تأیید" not in text
    assert "رد" not in text.replace("کرد", "")
