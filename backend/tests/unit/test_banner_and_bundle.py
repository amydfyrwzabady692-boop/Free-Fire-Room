"""The channel banner, and the follow screenshots that ride with a win claim."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import EventStatus, SocialPlatform, SocialProofStatus
from app.models.organizer import Organizer
from app.models.social import SocialProof
from app.models.user import User, UserProfile
from app.services.event_display import format_channel_post_caption, prize_places
from tests.conftest import make_event, make_organizer, make_user

# --- the prize, split into places -----------------------------------------


def test_one_line_is_one_place(db):
    host = make_user(db, 7001)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="واریز یک میلیون به کارت")
    assert prize_places(event) == ["واریز یک میلیون به کارت"]


def test_three_lines_become_the_podium(db):
    host = make_user(db, 7002)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="یک میلیون\n۵۰۰ هزار\n۱۰۰۰ الماس")
    assert prize_places(event) == ["یک میلیون", "۵۰۰ هزار", "۱۰۰۰ الماس"]


def test_a_fourth_place_is_dropped_rather_than_overflowing_the_card(db):
    host = make_user(db, 7003)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="a\nb\nc\nd")
    assert prize_places(event) == ["a", "b", "c"]


# --- the caption ----------------------------------------------------------


def test_the_caption_reads_the_way_the_owner_wrote_it(db):
    host = make_user(db, 7010)
    org = make_organizer(db, host)
    event = make_event(
        db,
        org,
        prize_summary="واریز یک میلیون 💵 به کارت\n۵۰۰ هزار تومان",
        starts_at=datetime.now(UTC) + timedelta(hours=3),
    )
    text = format_channel_post_caption(event, social_pages=2)
    assert text.startswith("🚨 کاستـوم جایـزه دار")
    assert "🥇" in text and "نفر اول" in text
    assert "🥈" in text and "نفر دوم" in text
    assert "زمان کاستوم 👇🔥" in text
    assert "شرایط و قوانین ❗" in text
    assert "✔️چیــت و تبــانی ممنوع 👎" in text
    assert "فالو ۲ پیج" in text or "فالو 2 پیج" in text
    assert text.rstrip().endswith("موفق و پیروز باشین 🥰")


def test_the_caption_never_outgrows_a_photo_caption(db):
    host = make_user(db, 7011)
    org = make_organizer(db, host)
    event = make_event(
        db,
        org,
        prize_summary=("ج" * 400),
        description=("د" * 500),
        starts_at=datetime.now(UTC) + timedelta(hours=3),
    )
    text = format_channel_post_caption(event, social_pages=5)
    assert len(text) <= 1024, len(text)


def test_a_prize_with_html_in_it_cannot_break_the_send(db):
    host = make_user(db, 7012)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="<b>یک میلیون</b> & بیشتر")
    text = format_channel_post_caption(event)
    assert "&lt;b&gt;" in text
    assert "&amp;" in text


# --- the poster image -----------------------------------------------------


def test_the_poster_carries_the_call_to_action():
    """The picture and the inline button under it must say the same thing."""
    from app.bot.keyboards.common import CHANNEL_POST_LABEL
    from app.services import posters

    assert posters.CTA == CHANNEL_POST_LABEL

    drawn: list[str] = []
    original = posters._center

    def _spy(draw, y, text, font, fill, *, max_w=None):
        drawn.append(text)
        return original(draw, y, text, font, fill, max_w=max_w)

    posters._center = _spy
    try:
        png = posters.render_event_poster(
            places=["یک میلیون", "۵۰۰ هزار", "۱۰۰۰ الماس"],
            when="امشب ساعت ۲۲",
            host="کلن ما",
            channels=3,
            social_pages=2,
            bot_username="ffroom",
        )
    finally:
        posters._center = original

    assert png.startswith(b"\x89PNG")
    body = "\n".join(drawn)
    assert "نفر اول: یک میلیون" in body
    assert "نفر سوم: ۱۰۰۰ الماس" in body
    assert "چیت و تبانی ممنوع" in body
    assert "t.me/ffroom" in body


def test_the_poster_survives_a_prize_nobody_should_have_typed():
    from app.services import posters

    png = posters.render_event_poster(
        prize="ج" * 300, when="امشب ساعت ۲۲", host="h", channels=8, bot_username="ffroom"
    )
    assert png.startswith(b"\x89PNG")


def test_truncation_keeps_the_start_of_a_persian_line():
    """Trimming the shaped string would eat the first word, not the last."""
    from PIL import Image, ImageDraw

    from app.services import posters

    draw = ImageDraw.Draw(Image.new("RGB", (100, 100)))
    font = posters._reg(30)
    raw = "واریز یک میلیون تومان به کارت برای نفر اول و دوم و سوم"
    fitted = posters._fit(draw, raw, font, 200)
    assert fitted.endswith("…")
    assert raw.startswith(fitted[:-1]), fitted


# --- the winner bundle ----------------------------------------------------


class Recorder:
    def __init__(self):
        self.photos: list = []
        self.sent: list = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))

    async def send_photo(self, chat_id, file_id, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, file_id, caption, reply_markup))


async def _seed(async_db, *, pages: list[str]):
    from app.core.security import generate_unguessable_token
    from app.models.event import Event
    from app.services.social import seed_tasks

    host = User(telegram_id=7100, first_name="host", username="hostguy")
    player = User(telegram_id=7101, first_name="player", username="lucky")
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
        starts_at=now - timedelta(minutes=10),
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status=EventStatus.PUBLISHED,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        prize_summary="۱۰۰ الماس",
        social_url=pages[0] if pages else None,
        social_platform=SocialPlatform.INSTAGRAM if pages else None,
    )
    async_db.add(event)
    await async_db.flush()
    if pages:
        seed_tasks(async_db, event, [{"url": u, "platform": None} for u in pages])
        await async_db.flush()
    return org, event, host, player


async def _proofs(async_db, event, player, *, statuses):
    from app.services.social import list_tasks

    tasks = await list_tasks(async_db, event.id)
    for task, status in zip(tasks, statuses, strict=False):
        async_db.add(
            SocialProof(
                event_id=event.id,
                user_id=player.id,
                task_id=task.id,
                file_id=f"follow-{task.sort_order}",
                status=status,
            )
        )
    await async_db.flush()


class _Claim:
    id = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_the_follow_screenshots_arrive_with_the_win_screenshot(async_db):
    """The organizer judges a prize claim with the follow proof in front of them."""
    from app.bot.handlers import winner as winner_panel

    org, event, host, player = await _seed(
        async_db, pages=["https://instagram.com/a", "https://youtube.com/@b"]
    )
    await _proofs(
        async_db,
        event,
        player,
        statuses=[SocialProofStatus.PENDING, SocialProofStatus.REJECTED],
    )
    await async_db.commit()

    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", _Claim())

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert len(to_host) == 3, "two follow shots plus the win shot"
    assert [p[1] for p in to_host] == ["follow-0", "follow-1", "win-shot"], (
        "the win screenshot must come last so the approve buttons stay in reach"
    )
    # each follow shot says which page it is, and can be rejected right there
    assert "اسکرین فالو ۱ از ۲" in to_host[0][2]
    assert "instagram.com/a" in to_host[0][2]
    assert "رد شده" in to_host[1][2], "a rejected proof is exactly what they need to see"
    assert any(
        b.callback_data and b.callback_data.startswith("socno:")
        for row in to_host[0][3].inline_keyboard
        for b in row
    )
    # the win shot keeps the claim buttons
    win_markup = to_host[2][3]
    assert any(
        b.callback_data and b.callback_data.startswith("orgw:ok:")
        for row in win_markup.inline_keyboard
        for b in row
    )
    assert "این عکس، اسکرین «برنده شدن» است" in to_host[2][2]


@pytest.mark.asyncio
async def test_a_custom_without_a_follow_gate_sends_one_photo(async_db):
    from app.bot.handlers import winner as winner_panel

    org, event, host, player = await _seed(async_db, pages=[])
    await async_db.commit()

    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", _Claim())

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert len(to_host) == 1
    assert to_host[0][1] == "win-shot"
    assert "شرط فالو نداشت" in to_host[0][2]


@pytest.mark.asyncio
async def test_a_missing_follow_screenshot_is_called_out(async_db):
    """Silence would read as "all good" - the organizer must be told."""
    from app.bot.handlers import winner as winner_panel

    org, event, host, player = await _seed(async_db, pages=["https://instagram.com/a"])
    await async_db.commit()

    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", _Claim())

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert len(to_host) == 1
    assert "هیچ اسکرین فالویی ثبت نشده" in to_host[0][2]
