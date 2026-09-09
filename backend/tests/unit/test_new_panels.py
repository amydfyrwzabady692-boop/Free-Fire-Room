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
    if social:
        # seed the page row too, so these tests exercise the same multi-page
        # path production does rather than the pre-0009 legacy branch
        from app.services.social import seed_tasks

        seed_tasks(
            async_db, event, [{"url": event.social_url, "platform": SocialPlatform.INSTAGRAM}]
        )
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


async def _seeded_proof(async_db, event, player, *, status=SocialProofStatus.PENDING):
    """A screenshot bound to the custom's page row, the way players send them."""
    from app.services.social import list_tasks

    tasks = await list_tasks(async_db, event.id)
    proof = SocialProof(
        event_id=event.id,
        user_id=player.id,
        task_id=tasks[0].id if tasks else None,
        file_id="shot",
        status=status,
    )
    async_db.add(proof)
    await async_db.flush()
    return proof


async def _confirmed_registration(async_db, event, player):
    from app.models.registration import Registration

    reg = Registration(
        event_id=event.id, user_id=player.id, status=RegistrationStatus.CONFIRMED, source="bot"
    )
    async_db.add(reg)
    event.confirmed_count = (event.confirmed_count or 0) + 1
    await async_db.flush()
    return reg


@pytest.mark.asyncio
async def test_rejecting_a_screenshot_voids_a_confirmed_entry(async_db):
    """A rejection has to take the seat back, not just recolour a row.

    Under the new rule the player was already registered when they sent the
    screenshot, so rejecting it must undo that or the organizer's only
    sanction does nothing at all.
    """
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player)
    reg = await _confirmed_registration(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_no(FakeCb(f"socno:{proof.id}", rec), async_db, host)

    await async_db.refresh(proof)
    await async_db.refresh(reg)
    assert proof.status == SocialProofStatus.REJECTED
    assert reg.status == RegistrationStatus.INELIGIBLE
    assert event.confirmed_count == 0
    told = [t for chat, t, _ in rec.sent if chat == player.telegram_id]
    assert told, "the player was never told their entry was voided"
    assert "دوباره" in told[0]


@pytest.mark.asyncio
async def test_undoing_a_rejection_gives_the_registration_back(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player, status=SocialProofStatus.REJECTED)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_ok(FakeCb(f"socok:{proof.id}", rec), async_db, host)

    await async_db.refresh(proof)
    assert proof.status == SocialProofStatus.APPROVED
    from sqlalchemy import select

    from app.models.registration import Registration

    reg = await async_db.scalar(
        select(Registration).where(
            Registration.event_id == event.id, Registration.user_id == player.id
        )
    )
    assert reg is not None and reg.status == RegistrationStatus.CONFIRMED
    assert any(chat == player.telegram_id for chat, _, _ in rec.sent)


@pytest.mark.asyncio
async def test_a_stranger_cannot_reject_a_follow_screenshot(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_no(FakeCb(f"socno:{proof.id}", rec), async_db, player)
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
async def test_a_follow_screenshot_is_filed_not_forwarded(async_db, monkeypatch):
    """It lands in the panel, and nobody's chat lights up.

    Nothing is waiting on the organizer, so a photo per player per page would
    only bury their own panel. The archive under the custom is where they look
    if they want to.
    """
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

    from app.services.social import get_proof, proof_counts_for_event

    proof = await get_proof(async_db, event_id=event.id, user_id=player.id)
    assert proof is not None
    assert proof.file_id == "shot-1"
    assert proof.status == SocialProofStatus.PENDING

    assert not [p for p in rec.photos if p[0] == host.telegram_id]
    assert not [m for m in rec.sent if m[0] == host.telegram_id]

    # but the organizer's panel knows it is there
    counts = await proof_counts_for_event(async_db, event.id)
    assert counts["total"] == 1
    assert counts["pending"] == 1


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


async def _send_screenshot(async_db, event, player, monkeypatch, rec):
    from app.bot.handlers import player as player_panel

    async def _no_limit(*a, **kw):
        return None

    monkeypatch.setattr(player_panel, "hit_rate_limit", _no_limit)
    state = FakeState()
    state.data["event_token"] = event.public_token
    await state.set_state("SocialProofSG:screenshot")
    msg = FakeMessage(rec)
    msg.photo = [type("P", (), {"file_id": "shot-1"})()]
    await player_panel.social_screenshot(msg, async_db, player, state)


@pytest.mark.asyncio
async def test_the_screenshot_itself_sends_the_room_straight_away(async_db, monkeypatch):
    """Sending is the moment they qualify, so the room must go out then.

    Waiting for the periodic sweep would leave a player who did everything
    right staring at nothing while the match starts.
    """
    org, event, host, player = await _seed(async_db, social=True)
    queued: list[str] = []
    from app.workers import enqueue

    monkeypatch.setattr(enqueue, "spawn", lambda task, *a: queued.append(a[0]))

    await _send_screenshot(async_db, event, player, monkeypatch, Recorder())
    assert queued == [str(event.id)], "the credentials send was never queued"


@pytest.mark.asyncio
async def test_no_send_is_queued_when_the_custom_is_already_closed(async_db, monkeypatch):
    org, event, host, player = await _seed(async_db, social=True)
    event.archived_at = datetime.now(UTC)
    await async_db.flush()

    queued: list[str] = []
    from app.workers import enqueue

    monkeypatch.setattr(enqueue, "spawn", lambda task, *a: queued.append(a[0]))

    await _send_screenshot(async_db, event, player, monkeypatch, Recorder())
    assert queued == []


# ------------------------------------------------- the follow-screenshot archive


async def _many_proofs(async_db, event, players, *, statuses):
    from app.services.social import list_tasks

    tasks = await list_tasks(async_db, event.id)
    made = []
    for i, status in enumerate(statuses):
        user = User(telegram_id=6200 + i, first_name=f"p{i}")
        async_db.add(user)
        await async_db.flush()
        proof = SocialProof(
            event_id=event.id,
            user_id=user.id,
            task_id=tasks[0].id if tasks else None,
            file_id=f"shot-{i}",
            status=status,
        )
        async_db.add(proof)
        made.append(proof)
    await async_db.flush()
    return made


@pytest.mark.asyncio
async def test_the_archive_still_opens_when_nothing_is_pending(async_db):
    """The old queue emptied itself into a dead end; the archive must not."""
    org, event, host, player = await _seed(async_db, social=True)
    await _many_proofs(
        async_db,
        event,
        None,
        statuses=[SocialProofStatus.APPROVED, SocialProofStatus.APPROVED],
    )
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_queue(FakeCb(f"orgp:soc:{event.public_token}", rec), async_db, host)
    text = rec.last
    assert "اسکرین‌های فالو" in text
    assert "بررسی‌نشده: 0" in text
    assert "لازم نیست چیزی را تأیید کنید" in text


@pytest.mark.asyncio
async def test_the_archive_pages_instead_of_flooding_the_chat(async_db):
    """Twelve screenshots used to mean twelve separate photo messages."""
    org, event, host, player = await _seed(async_db, social=True)
    await _many_proofs(async_db, event, None, statuses=[SocialProofStatus.PENDING] * 12)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_queue(FakeCb(f"orgp:soc:{event.public_token}", rec), async_db, host)
    assert len(rec.views) == 1, "one edited view, not one message per screenshot"
    assert not rec.photos, "photos only load when the organizer asks for one"
    kb = rec.views[-1][1]
    data = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
    assert sum(1 for d in data if d.startswith("socv:")) == 5
    assert any(d == f"orgp:soc:{event.public_token}:1:a" for d in data), "no next page"


@pytest.mark.asyncio
async def test_the_archive_can_be_filtered_to_the_rejected_ones(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    await _many_proofs(
        async_db,
        event,
        None,
        statuses=[SocialProofStatus.PENDING, SocialProofStatus.REJECTED, SocialProofStatus.APPROVED],
    )
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_queue(
        FakeCb(f"orgp:soc:{event.public_token}:0:r", rec), async_db, host
    )
    kb = rec.views[-1][1]
    data = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
    assert sum(1 for d in data if d.startswith("socv:")) == 1


@pytest.mark.asyncio
async def test_one_screenshot_opens_full_size_with_a_reject_button(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_view(FakeCb(f"socv:{proof.id.hex}", rec), async_db, host)
    assert rec.photos
    _, file_id, caption, markup = rec.photos[-1]
    assert file_id == "shot"
    assert "بررسی‌نشده" in caption
    data = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert f"socno:{proof.id}" in data
    assert f"orgp:soc:{event.public_token}" in data, "no way back to the list"


@pytest.mark.asyncio
async def test_a_stranger_cannot_open_someone_elses_screenshot(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_view(FakeCb(f"socv:{proof.id.hex}", rec), async_db, player)
    assert not rec.photos
    assert any(alert for alert, _ in rec.alerts)


@pytest.mark.asyncio
async def test_marking_everything_checked_changes_nobody_s_registration(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proofs = await _many_proofs(
        async_db, event, None, statuses=[SocialProofStatus.PENDING] * 3
    )
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_all_ok(
        FakeCb(f"socall:ok:{event.public_token}", rec), async_db, host
    )
    for proof in proofs:
        await async_db.refresh(proof)
        assert proof.status == SocialProofStatus.APPROVED
    assert event.confirmed_count == 0
    assert "چیزی برای بازیکن‌ها عوض نشد" in rec.last


@pytest.mark.asyncio
async def test_rejecting_everyone_asks_first(async_db):
    """One mis-tap would otherwise void a whole custom."""
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_all_ask(
        FakeCb(f"socall:ask:{event.public_token}", rec), async_db, host
    )
    await async_db.refresh(proof)
    assert proof.status == SocialProofStatus.PENDING, "nothing may happen before the confirm"
    assert "مطمئنید" in rec.last
    data = [
        b.callback_data
        for row in rec.views[-1][1].inline_keyboard
        for b in row
        if b.callback_data
    ]
    assert f"socall:no:{event.public_token}" in data


@pytest.mark.asyncio
async def test_confirming_reject_all_voids_the_confirmed_seats(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player)
    reg = await _confirmed_registration(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_all_no(
        FakeCb(f"socall:no:{event.public_token}", rec), async_db, host
    )
    await async_db.refresh(proof)
    await async_db.refresh(reg)
    assert proof.status == SocialProofStatus.REJECTED
    assert reg.status == RegistrationStatus.INELIGIBLE
    assert event.confirmed_count == 0


@pytest.mark.asyncio
async def test_the_archive_lists_every_screenshot_not_only_the_unreviewed(async_db):
    """Once nothing is pending the old queue vanished; the archive must not."""
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player, status=SocialProofStatus.APPROVED)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_queue(FakeCb(f"orgp:soc:{event.public_token}", rec), async_db, host)

    text = rec.last
    assert "بررسی‌نشده: 0" in text
    assert "همه: 1" in text
    kb = rec.views[-1][1]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
    assert f"socv:{proof.id.hex}" in callbacks, "the screenshot itself must be reachable"


@pytest.mark.asyncio
async def test_the_archive_does_not_claim_the_organizer_is_blocking_anyone(async_db):
    org, event, host, player = await _seed(async_db, social=True)
    await _seeded_proof(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_queue(FakeCb(f"orgp:soc:{event.public_token}", rec), async_db, host)
    assert "لازم نیست چیزی را تأیید کنید" in rec.last


@pytest.mark.asyncio
async def test_a_rejected_player_gets_their_seat_back_by_resending(async_db, monkeypatch):
    """The whole point of rejecting is that it can be fixed."""
    from app.bot.handlers import player as player_panel
    from app.services.social import get_proof

    async def _no_limit(*a, **kw):
        return None

    monkeypatch.setattr(player_panel, "hit_rate_limit", _no_limit)
    org, event, host, player = await _seed(async_db, social=True)
    proof = await _seeded_proof(async_db, event, player)
    reg = await _confirmed_registration(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_social_no(FakeCb(f"socno:{proof.id}", rec), async_db, host)
    await async_db.refresh(reg)
    assert reg.status == RegistrationStatus.INELIGIBLE
    assert event.confirmed_count == 0

    # the player sends a real screenshot this time
    state = FakeState()
    state.data["event_token"] = event.public_token
    await state.set_state("SocialProofSG:screenshot")
    msg = FakeMessage(Recorder())
    msg.photo = [type("P", (), {"file_id": "better-shot"})()]
    await player_panel.social_screenshot(msg, async_db, player, state)

    await async_db.refresh(reg)
    fixed = await get_proof(async_db, event_id=event.id, user_id=player.id)
    assert fixed.status == SocialProofStatus.PENDING
    assert fixed.file_id == "better-shot"
    assert reg.status == RegistrationStatus.CONFIRMED
    assert reg.ineligible_reason is None, "the old reason must not follow them"
    assert event.confirmed_count == 1, "the seat must come back exactly once"


# --- one custom, one screen ------------------------------------------------


def _kb_callbacks(markup) -> list:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


@pytest.mark.asyncio
async def test_my_customs_is_a_list_not_a_pile_of_messages(async_db):
    """Fifteen customs used to mean sixteen messages fired in a loop.

    Telegram throttles around one message per second per chat, so the tail
    arrived late, out of order, or not at all.
    """
    org, event, host, player = await _seed(async_db)
    rec = Recorder()
    await org_panel.org_mine(FakeCb("orgp:mine", rec), async_db, host)

    assert len(rec.views) == 1, "the list is one message, edited in place"
    text, markup = rec.views[0]
    assert f"orgp:ev:{event.public_token}" in _kb_callbacks(markup)
    assert "روی هر کاستوم بزنید" in text


@pytest.mark.asyncio
async def test_the_detail_screen_carries_every_action(async_db):
    """Whatever the organizer came to do, it is on this screen."""
    org, event, host, player = await _seed(async_db, social=True)
    await _seeded_proof(async_db, event, player)
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_event_detail(FakeCb(f"orgp:ev:{event.public_token}", rec), async_db, host)

    text, markup = rec.views[-1]
    data = _kb_callbacks(markup)
    token = event.public_token
    for expected in (
        f"orgp:creds:{token}",
        f"orgp:post:{token}",
        f"orgp:soc:{token}",
        f"orgp:link:{token}",
        f"orgp:fun:{token}",
        f"orgp:csv:{token}",
        f"orgp:rep:{token}",
        f"orgp:start:{token}",
        f"orgp:cancel:{token}",
        "orgp:mine",
    ):
        assert expected in data, f"{expected} is not reachable from the custom's screen"


@pytest.mark.asyncio
async def test_a_wrong_room_id_can_be_corrected_and_the_button_says_so(async_db):
    """The owner's case: they typed the ROOM ID wrong and need it fixed."""
    org, event, host, player = await _seed(async_db, with_creds=True)
    rec = Recorder()
    await org_panel.org_event_detail(FakeCb(f"orgp:ev:{event.public_token}", rec), async_db, host)

    text, markup = rec.views[-1]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("اصلاح ROOM ID" in label for label in labels), labels
    assert "ROOM ID / PASS: ثبت شده" in text
    assert f"orgp:creds:{event.public_token}" in _kb_callbacks(markup)


@pytest.mark.asyncio
async def test_before_any_credentials_the_button_offers_to_send_them(async_db):
    org, event, host, player = await _seed(async_db, with_creds=False)
    rec = Recorder()
    await org_panel.org_event_detail(FakeCb(f"orgp:ev:{event.public_token}", rec), async_db, host)

    text, markup = rec.views[-1]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("ارسال ROOM ID" in label for label in labels), labels
    assert "هنوز ثبت نشده" in text


@pytest.mark.asyncio
async def test_the_detail_screen_is_not_someone_elses_to_open(async_db):
    _, event, _, player = await _seed(async_db)
    rec = Recorder()
    await org_panel.org_event_detail(FakeCb(f"orgp:ev:{event.public_token}", rec), async_db, player)
    assert not rec.views
    assert rec.alerts


@pytest.mark.asyncio
async def test_winners_can_be_read_one_custom_at_a_time(async_db):
    org, event, host, player = await _seed(async_db)
    async_db.add(
        WinnerClaim(
            event_id=event.id,
            user_id=player.id,
            organizer_id=org.id,
            screenshot_file_id="win",
            status=WinnerClaimStatus.PENDING,
        )
    )
    await async_db.commit()

    rec = Recorder()
    await org_panel.org_event_detail(FakeCb(f"orgp:ev:{event.public_token}", rec), async_db, host)
    assert f"orgp:evwin:{event.public_token}" in _kb_callbacks(rec.views[-1][1])

    rec2 = Recorder()
    await org_panel.org_event_winners(
        FakeCb(f"orgp:evwin:{event.public_token}", rec2), async_db, host
    )
    assert [p[1] for p in rec2.photos] == ["win"]
    # and back goes to that custom, not to the panel root
    assert f"orgp:ev:{event.public_token}" in _kb_callbacks(rec2.views[-1][1])
