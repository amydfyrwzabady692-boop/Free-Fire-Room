"""Drive the new bot handlers directly: start button, follow review, winner relay.

These are the taps a real organizer makes, so they catch the wiring mistakes a
service-level test cannot - a missing callback prefix, a keyboard that offers a
button the handler does not accept, a notification that never goes out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.bot.handlers import organizer as org_panel
from app.bot.handlers import player as player_panel
from app.core.enums import (
    EventStatus,
    RegistrationStatus,
    SocialPlatform,
    SocialProofStatus,
    WinnerClaimStatus,
)
from app.models.event import RoomCredential
from app.models.organizer import Organizer
from app.models.social import SocialProof
from app.models.user import User, UserProfile
from app.models.winner import WinnerClaim


class Recorder:
    def __init__(self):
        self.views: list = []
        self.alerts: list = []
        self.photos: list = []
        self.sent: list = []
        self.id = 1

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))

    async def send_photo(self, chat_id, file_id, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, file_id, caption, reply_markup))

    @property
    def last(self) -> str:
        assert self.views, "handler produced no view"
        return self.views[-1][0]

    def all_text(self) -> str:
        return "\n".join(v[0] for v in self.views) + "\n".join(t for _, t, _ in self.sent)


class FakeMessage:
    def __init__(self, rec, text="x"):
        self.rec = rec
        self.text = text
        self.caption = None
        self.photo = None
        self.document = None
        self.bot = rec
        self.chat = type("C", (), {"id": 1})()

    async def answer(self, text, reply_markup=None, **kw):
        self.rec.views.append((text, reply_markup))

    async def answer_photo(self, file_id, caption=None, reply_markup=None, **kw):
        self.rec.photos.append((None, file_id, caption, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.rec.views.append((text, reply_markup))

    async def answer_document(self, *a, **kw):
        pass


class FakeCb:
    def __init__(self, data, rec, user_id=1):
        self.data = data
        self.rec = rec
        self.message = FakeMessage(rec)
        self.bot = rec
        self.from_user = type("U", (), {"id": user_id})()

    async def answer(self, text="", show_alert=False):
        self.rec.alerts.append((text, show_alert))


class FakeState:
    def __init__(self):
        self.state = None
        self.data: dict = {}

    async def set_state(self, state):
        self.state = getattr(state, "state", state)

    async def get_state(self):
        return self.state

    async def update_data(self, **kw):
        self.data.update(kw)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state = None
        self.data = {}


async def _seed(async_db, *, social=False, minutes_ago=10, with_creds=True):
    from app.core.security import generate_unguessable_token
    from app.models.event import Event

    host = User(telegram_id=6100, first_name="host", username="hostguy")
    player = User(telegram_id=6101, first_name="player", username="lucky")
    async_db.add_all([host, player])
    await async_db.flush()
    async_db.add_all([UserProfile(user_id=host.id), UserProfile(user_id=player.id)])
    org = Organizer(user_id=host.id, status="approved", display_name="Host")
    async_db.add(org)
    await async_db.flush()
    now = datetime.now(UTC)
    event = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="Custom",
        starts_at=now - timedelta(minutes=minutes_ago),
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status=EventStatus.PUBLISHED,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        prize_summary="۱۰۰ الماس",
        social_url="https://instagram.com/page" if social else None,
        social_platform=SocialPlatform.INSTAGRAM if social else None,
    )
    async_db.add(event)
    await async_db.flush()
    if with_creds:
        from app.core.security import encrypt_secret

        async_db.add(
            RoomCredential(
                event_id=event.id,
                room_id_encrypted=encrypt_secret("12345678"),
                room_password_encrypted=encrypt_secret("pass"),
            )
        )
        await async_db.flush()
    await async_db.commit()
    return org, event, host, player


@pytest.mark.asyncio
async def test_start_button_archives_and_tells_the_organizer(async_db):
    org, event, host, _ = await _seed(async_db)
    rec = Recorder()
    cb = FakeCb(f"orgp:start:{event.public_token}", rec)
    await org_panel.org_mark_started(cb, async_db, host)
    await async_db.refresh(event)
    assert event.archived_at is not None
    assert "گذشته" in rec.last


@pytest.mark.asyncio
async def test_start_button_refuses_before_credentials_exist(async_db):
    org, event, host, _ = await _seed(async_db, with_creds=False)
    rec = Recorder()
    cb = FakeCb(f"orgp:start:{event.public_token}", rec)
    await org_panel.org_mark_started(cb, async_db, host)
    await async_db.refresh(event)
    assert event.archived_at is None
    assert any("ROOM ID" in text for text, _ in rec.alerts)


@pytest.mark.asyncio
async def test_start_button_is_not_offered_to_someone_elses_custom(async_db):
    _, event, _, player = await _seed(async_db)
    rec = Recorder()
    cb = FakeCb(f"orgp:start:{event.public_token}", rec)
    await org_panel.org_mark_started(cb, async_db, player)
    await async_db.refresh(event)
    assert event.archived_at is None


@pytest.mark.asyncio
async def test_approving_a_follow_screenshot_confirms_the_registration(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = SocialProof(
        event_id=event.id, user_id=player.id, file_id="shot", status=SocialProofStatus.PENDING
    )
    async_db.add(proof)
    await async_db.commit()

    rec = Recorder()
    cb = FakeCb(f"socok:{proof.id}", rec)
    await org_panel.org_social_ok(cb, async_db, host)

    await async_db.refresh(proof)
    assert proof.status == SocialProofStatus.APPROVED
    from app.models.registration import Registration
    from sqlalchemy import select

    reg = await async_db.scalar(
        select(Registration).where(
            Registration.event_id == event.id, Registration.user_id == player.id
        )
    )
    assert reg is not None
    assert reg.status == RegistrationStatus.CONFIRMED
    # the player is told, on their own chat
    assert any(chat == player.telegram_id for chat, _, _ in rec.sent)


@pytest.mark.asyncio
async def test_rejecting_a_follow_screenshot_leaves_them_pending(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = SocialProof(
        event_id=event.id, user_id=player.id, file_id="shot", status=SocialProofStatus.PENDING
    )
    async_db.add(proof)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_no(FakeCb(f"socno:{proof.id}", rec), async_db, host)
    await async_db.refresh(proof)
    assert proof.status == SocialProofStatus.REJECTED
    assert event.confirmed_count == 0


@pytest.mark.asyncio
async def test_a_stranger_cannot_approve_a_follow_screenshot(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = SocialProof(
        event_id=event.id, user_id=player.id, file_id="shot", status=SocialProofStatus.PENDING
    )
    async_db.add(proof)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_ok(FakeCb(f"socok:{proof.id}", rec), async_db, player)
    await async_db.refresh(proof)
    assert proof.status == SocialProofStatus.PENDING


@pytest.mark.asyncio
async def test_approving_a_winner_sends_the_payout_contact(async_db):
    org, event, host, player = await _seed(async_db)
    org.payout_contact = "@payme"
    claim = WinnerClaim(
        event_id=event.id, user_id=player.id, organizer_id=org.id, screenshot_file_id="shot"
    )
    async_db.add(claim)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_winner_ok(FakeCb(f"orgw:ok:{claim.id}", rec), async_db, host)

    await async_db.refresh(claim)
    assert claim.status == WinnerClaimStatus.APPROVED
    to_player = [t for chat, t, _ in rec.sent if chat == player.telegram_id]
    assert to_player and "@payme" in to_player[0]
    # and the winner gets a way to answer back
    markup = [m for chat, _, m in rec.sent if chat == player.telegram_id][0]
    assert any(
        b.callback_data == f"winr:{claim.id}" for row in markup.inline_keyboard for b in row
    )


@pytest.mark.asyncio
async def test_organizer_message_reaches_the_winner(async_db):
    org, event, host, player = await _seed(async_db)
    claim = WinnerClaim(
        event_id=event.id, user_id=player.id, organizer_id=org.id, screenshot_file_id="shot"
    )
    async_db.add(claim)
    await async_db.commit()

    rec = Recorder()
    state = FakeState()
    await org_panel.org_winner_message(FakeCb(f"orgw:msg:{claim.id}", rec), async_db, host, state)
    assert state.data["claim_id"] == str(claim.id)

    msg = FakeMessage(rec, text="شماره کارتت را بفرست")
    await org_panel.org_winner_message_body(msg, state, async_db, host)
    to_player = [t for chat, t, _ in rec.sent if chat == player.telegram_id]
    assert to_player and "شماره کارتت" in to_player[0]

    from app.models.winner import WinnerMessage
    from sqlalchemy import select

    rows = list((await async_db.scalars(select(WinnerMessage))).all())
    assert len(rows) == 1
    assert rows[0].delivered is True


@pytest.mark.asyncio
async def test_a_stranger_cannot_message_a_winner(async_db):
    org, event, host, player = await _seed(async_db)
    claim = WinnerClaim(
        event_id=event.id, user_id=player.id, organizer_id=org.id, screenshot_file_id="shot"
    )
    async_db.add(claim)
    await async_db.commit()

    rec = Recorder()
    state = FakeState()
    await org_panel.org_winner_message(FakeCb(f"orgw:msg:{claim.id}", rec), async_db, player, state)
    assert state.state is None
    assert rec.alerts


@pytest.mark.asyncio
async def test_player_follow_screenshot_reaches_the_organizer(async_db, monkeypatch):
    async def _no_limit(*a, **kw):
        return None

    monkeypatch.setattr(player_panel, "hit_rate_limit", _no_limit)
    org, event, host, player = await _seed(async_db, social=True)
    rec = Recorder()
    state = FakeState()
    state.data["event_token"] = event.public_token
    await state.set_state("SocialProofSG:screenshot")

    msg = FakeMessage(rec)
    msg.photo = [type("P", (), {"file_id": "shot-1"})()]
    await player_panel.social_screenshot(msg, async_db, player, state)

    from app.services.social import get_proof

    proof = await get_proof(async_db, event_id=event.id, user_id=player.id)
    assert proof is not None
    assert proof.status == SocialProofStatus.PENDING
    # the organizer is shown the screenshot with review buttons
    assert rec.photos
    _, file_id, _, markup = rec.photos[-1]
    assert file_id == "shot-1"
    assert any(
        b.callback_data == f"socok:{proof.id}" for row in markup.inline_keyboard for b in row
    )


@pytest.mark.asyncio
async def test_owner_panel_approval_also_hands_over_the_contact(async_db):
    """The bot owner can settle a claim when the organizer goes quiet."""
    from app.bot.handlers import admin as admin_panel
    from app.models.admin import Admin

    org, event, host, player = await _seed(async_db)
    org.payout_contact = "@payme"
    owner = User(telegram_id=6200, first_name="owner")
    async_db.add(owner)
    await async_db.flush()
    async_db.add_all([UserProfile(user_id=owner.id), Admin(user_id=owner.id, is_active=True)])
    claim = WinnerClaim(
        event_id=event.id, user_id=player.id, organizer_id=org.id, screenshot_file_id="shot"
    )
    async_db.add(claim)
    await async_db.commit()

    rec = Recorder()
    await admin_panel._resolve_winner(FakeCb(f"adm:wok:{claim.id}", rec), async_db, owner, True)

    await async_db.refresh(claim)
    assert claim.status == WinnerClaimStatus.APPROVED
    to_player = [t for chat, t, _ in rec.sent if chat == player.telegram_id]
    assert to_player and "@payme" in to_player[0]


@pytest.mark.asyncio
async def test_claim_notification_offers_the_players_dm(async_db):
    """The organizer asked to be able to just open the winner's chat."""
    from app.bot.handlers import winner as winner_panel

    org, event, host, player = await _seed(async_db)
    claim = WinnerClaim(
        event_id=event.id, user_id=player.id, organizer_id=org.id, screenshot_file_id="shot"
    )
    async_db.add(claim)
    await async_db.commit()

    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "shot", claim)

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert to_host, "the organizer was not shown the screenshot"
    caption, markup = to_host[0][2], to_host[0][3]
    assert str(player.telegram_id) in caption
    urls = [b.url for row in markup.inline_keyboard for b in row if b.url]
    assert "https://t.me/lucky" in urls


@pytest.mark.asyncio
async def test_approving_a_follow_screenshot_sends_the_room_straight_away(async_db, monkeypatch):
    """Approval is the moment they qualify, so the room must go out then.

    Waiting for the periodic sweep would leave a player who did everything
    right staring at nothing while the match starts.
    """
    org, event, host, player = await _seed(async_db, social=True)
    proof = SocialProof(
        event_id=event.id, user_id=player.id, file_id="shot", status=SocialProofStatus.PENDING
    )
    async_db.add(proof)
    await async_db.commit()

    queued: list[str] = []
    from app.workers import enqueue

    monkeypatch.setattr(enqueue, "spawn", lambda task, *a: queued.append(a[0]))

    rec = Recorder()
    await org_panel.org_social_ok(FakeCb(f"socok:{proof.id}", rec), async_db, host)

    assert queued == [str(event.id)], "the credentials send was never queued"


@pytest.mark.asyncio
async def test_no_send_is_queued_when_the_custom_is_already_closed(async_db, monkeypatch):
    org, event, host, player = await _seed(async_db, social=True)
    event.archived_at = datetime.now(UTC)
    proof = SocialProof(
        event_id=event.id, user_id=player.id, file_id="shot", status=SocialProofStatus.PENDING
    )
    async_db.add(proof)
    await async_db.commit()

    queued: list[str] = []
    from app.workers import enqueue

    monkeypatch.setattr(enqueue, "spawn", lambda task, *a: queued.append(a[0]))

    rec = Recorder()
    await org_panel.org_social_ok(FakeCb(f"socok:{proof.id}", rec), async_db, host)
    assert queued == []
