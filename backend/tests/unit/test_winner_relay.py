"""Approving a winner hands them a contact, and both sides can talk back."""

from datetime import UTC, datetime

from app.core.enums import WinnerClaimStatus, WinnerMessageDirection
from app.models.organizer import Organizer
from app.models.user import User, UserProfile
from app.models.winner import WinnerClaim, WinnerMessage
from app.services.winners import (
    claim_parties,
    contact_link,
    format_payout_note,
    format_relayed_to_organizer,
    format_relayed_to_winner,
    record_message,
    resolve_claim,
    resolve_payout_contact,
)


async def _seed(async_db, *, event_contact=None, org_contact=None, org_username=None):
    from app.core.security import generate_unguessable_token
    from app.models.event import Event

    host = User(telegram_id=7100, first_name="host", username=org_username)
    winner = User(telegram_id=7101, first_name="winner", username="lucky")
    async_db.add_all([host, winner])
    await async_db.flush()
    async_db.add_all([UserProfile(user_id=host.id), UserProfile(user_id=winner.id)])
    org = Organizer(user_id=host.id, status="approved", payout_contact=org_contact)
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
        prize_summary="۱۰۰ هزار تومان",
        payout_contact=event_contact,
    )
    async_db.add(event)
    await async_db.flush()
    claim = WinnerClaim(
        event_id=event.id,
        user_id=winner.id,
        organizer_id=org.id,
        screenshot_file_id="shot",
    )
    async_db.add(claim)
    await async_db.flush()
    return org, event, winner, host, claim


async def test_payout_contact_prefers_the_event_then_the_organizer(async_db):
    _, event, _, _, _ = await _seed(async_db, event_contact="@from_event", org_contact="@from_org")
    assert await resolve_payout_contact(async_db, event) == "@from_event"


async def test_payout_contact_falls_back_to_the_saved_one(async_db):
    _, event, _, _, _ = await _seed(async_db, org_contact="@from_org")
    assert await resolve_payout_contact(async_db, event) == "@from_org"


async def test_payout_contact_falls_back_to_the_organizer_username(async_db):
    _, event, _, _, _ = await _seed(async_db, org_username="hostguy")
    assert await resolve_payout_contact(async_db, event) == "@hostguy"


async def test_payout_contact_may_be_missing(async_db):
    _, event, _, _, _ = await _seed(async_db)
    assert await resolve_payout_contact(async_db, event) is None
    note = format_payout_note(event, None)
    assert "هنوز آیدی دریافت جایزه را ثبت نکرده" in note


async def test_approved_winner_is_told_where_to_go(async_db):
    _, event, winner, _, claim = await _seed(async_db, org_contact="@payme")
    await resolve_claim(async_db, claim, approved=True, reviewer_id=winner.id)
    assert claim.status == WinnerClaimStatus.APPROVED
    assert claim.reviewed_at is not None
    contact = await resolve_payout_contact(async_db, event)
    note = format_payout_note(event, contact)
    assert "@payme" in note
    assert "۱۰۰ هزار تومان" in note
    assert contact_link(contact) == "https://t.me/payme"


async def test_claim_parties_resolves_both_sides(async_db):
    _, _, winner, host, claim = await _seed(async_db)
    got_winner, got_org = await claim_parties(async_db, claim)
    assert got_winner.id == winner.id
    assert got_org.id == host.id


async def test_messages_are_recorded_in_both_directions(async_db):
    _, event, winner, host, claim = await _seed(async_db)
    await record_message(async_db, claim=claim, sender_id=host.id, body="سلام", delivered=True)
    await record_message(
        async_db,
        claim=claim,
        sender_id=winner.id,
        body="ممنون",
        delivered=True,
        direction=WinnerMessageDirection.TO_ORGANIZER,
    )
    rows = list(
        (
            await async_db.scalars(
                WinnerMessage.__table__.select().where(WinnerMessage.claim_id == claim.id)
            )
        ).all()
    )
    assert len(rows) == 2

    assert "سلام" in format_relayed_to_winner(event, "سلام")
    body = format_relayed_to_organizer(event, winner, "ممنون")
    assert "ممنون" in body
    assert "7101" in body  # the organizer can see who to reply to


async def test_long_messages_are_capped(async_db):
    _, _, _, host, claim = await _seed(async_db)
    row = await record_message(
        async_db, claim=claim, sender_id=host.id, body="x" * 9000, delivered=False
    )
    assert len(row.body) == 4000
