"""The channel banner, and the follow screenshots that ride with a win claim."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.bot.keyboards.common import CHANNEL_POST_LABEL, channel_post_kb
from app.core.enums import EventStatus, SocialPlatform, SocialProofStatus
from app.models.organizer import Organizer
from app.models.social import SocialProof
from app.models.user import User, UserProfile
from app.services.event_display import POST_LIMIT, format_channel_post_caption, prize_places
from tests.conftest import make_event, make_organizer, make_user

# --- the prize, read the way the organizer typed it ------------------------


def test_one_line_of_prize_is_one_place(db):
    host = make_user(db, 7101)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="واریز یک میلیون تومان به کارت")
    assert prize_places(event) == ["واریز یک میلیون تومان به کارت"]


def test_three_lines_become_the_podium(db):
    host = make_user(db, 7102)
    org = make_organizer(db, host)
    event = make_event(
        db, org, prize_summary="واریز یک میلیون به کارت\n۵۰۰ هزار تومان\n۱۰۰۰ الماس"
    )
    assert prize_places(event) == ["واریز یک میلیون به کارت", "۵۰۰ هزار تومان", "۱۰۰۰ الماس"]


def test_a_fourth_place_is_dropped_rather_than_squeezed(db):
    host = make_user(db, 7103)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="یک\nدو\nسه\nچهار")
    assert prize_places(event) == ["یک", "دو", "سه"]


# --- the post text ---------------------------------------------------------


def _caption(db, telegram_id, **kw):
    host = make_user(db, telegram_id)
    org = make_organizer(db, host)
    event = make_event(db, org, **kw)
    return event, format_channel_post_caption(event)


def test_the_post_reads_like_the_owner_wrote_it(db):
    event, text = _caption(db, 7110, prize_summary="واریز یک میلیون به کارت\n۵۰۰ هزار تومان")
    lines = [line for line in text.split("\n") if line.strip()]
    assert lines[0] == "🚨 کاستـوم جایـزه دار"
    assert "🥇" in text and "نفر اول" in text
    assert "🥈" in text and "نفر دوم" in text
    assert "زمان کاستوم 👇🔥" in text
    assert "شرایط و قوانین ❗" in text
    assert "✔️چیــت و تبــانی ممنوع 👎" in text
    assert lines[-1] == "موفق و پیروز باشین 🥰"


def test_the_post_names_the_follow_pages_when_there_are_any(db):
    host = make_user(db, 7111)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="۱۰۰۰ الماس")
    with_pages = format_channel_post_caption(event, social_pages=2)
    without = format_channel_post_caption(event, social_pages=0)
    assert "فالو ۲ پیج" in with_pages
    assert "پیج" not in without.replace("کاستوم", "")


def test_the_post_stays_short_enough_to_read(db):
    host = make_user(db, 7112)
    org = make_organizer(db, host)
    event = make_event(
        db,
        org,
        prize_summary="\n".join("جایزهٔ بسیار طولانی برای این نفر " * 6 for _ in range(3)),
        description="توضیح خیلی طولانی " * 60,
    )
    text = format_channel_post_caption(event, social_pages=5)
    assert len(text) <= POST_LIMIT, len(text)


def test_a_prize_with_html_in_it_cannot_break_the_post(db):
    _, text = _caption(db, 7113, prize_summary="<b>یک میلیون</b> & بیشتر")
    assert "<b>یک میلیون</b>" not in text
    assert "&lt;b&gt;" in text and "&amp;" in text


# --- the button under the post ---------------------------------------------


def test_the_post_uses_persian_digits_throughout(db):
    """A post that mixes ۲۲:۰۰ with "3 کانال" reads like two people wrote it."""
    from app.models.channel import Channel
    from app.models.event import EventRequiredChannel

    host = make_user(db, 7120)
    org = make_organizer(db, host)
    event = make_event(db, org, prize_summary="۱۰۰۰ الماس")
    channel = Channel(telegram_chat_id=-100777, title="ch", bot_is_admin=True)
    db.add(channel)
    db.flush()
    db.add(EventRequiredChannel(event_id=event.id, channel_id=channel.id, is_active=True))
    db.flush()

    text = format_channel_post_caption(event, social_pages=2)
    assert "۱ کانال" in text
    assert "۲ پیج" in text
    assert not any(ch in text for ch in "0123456789")


def test_the_channel_button_is_a_plain_url_button():
    kb = channel_post_kb("https://t.me/ffroom?start=event_abc")
    button = kb.inline_keyboard[0][0]
    assert button.text == "ورود به کاستوم جایزه دار"
    assert button.text == CHANNEL_POST_LABEL
    assert button.url.endswith("?start=event_abc")
    assert len(kb.inline_keyboard) == 1, "one button under the post, nothing else"


# --- the winner bundle -----------------------------------------------------


class Recorder:
    def __init__(self):
        self.photos: list = []
        self.sent: list = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))

    async def send_photo(self, chat_id, file_id, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, file_id, caption, reply_markup))


async def _claim_setup(async_db, *, social: bool, pages: int = 2):
    from app.core.security import generate_unguessable_token
    from app.models.event import Event
    from app.models.winner import WinnerClaim
    from app.services.social import list_tasks, seed_tasks

    host = User(telegram_id=7200, first_name="host", username="hostguy")
    player = User(telegram_id=7201, first_name="player", username="lucky")
    async_db.add_all([host, player])
    await async_db.flush()
    async_db.add_all([UserProfile(user_id=host.id), UserProfile(user_id=player.id)])
    org = Organizer(user_id=host.id, status="approved", display_name="Host")
    async_db.add(org)
    await async_db.flush()
    now = datetime.now(UTC)
    urls = [f"https://instagram.com/p{i}" for i in range(pages)]
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
        prize_summary="۱۰۰۰ الماس",
        social_url=urls[0] if social else None,
        social_platform=SocialPlatform.INSTAGRAM if social else None,
    )
    async_db.add(event)
    await async_db.flush()
    if social:
        seed_tasks(async_db, event, [{"url": u, "platform": SocialPlatform.INSTAGRAM} for u in urls])
        await async_db.flush()
        for task in await list_tasks(async_db, event.id):
            async_db.add(
                SocialProof(
                    event_id=event.id,
                    user_id=player.id,
                    task_id=task.id,
                    file_id=f"follow-{task.sort_order}",
                    status=SocialProofStatus.PENDING,
                )
            )
    claim = WinnerClaim(event_id=event.id, user_id=player.id, screenshot_file_id="win-shot")
    async_db.add(claim)
    await async_db.flush()
    return event, host, player, claim


@pytest.mark.asyncio
async def test_the_follow_screenshots_arrive_with_the_win_screenshot(async_db):
    from app.bot.handlers import winner as winner_panel

    event, host, player, claim = await _claim_setup(async_db, social=True, pages=2)
    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", claim)

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert len(to_host) == 3, "two follow shots plus the win shot"
    assert [p[1] for p in to_host] == ["follow-0", "follow-1", "win-shot"]


@pytest.mark.asyncio
async def test_the_win_screenshot_goes_last_so_the_buttons_stay_reachable(async_db):
    from app.bot.handlers import winner as winner_panel

    event, host, player, claim = await _claim_setup(async_db, social=True, pages=2)
    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", claim)

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    last_markup = to_host[-1][3]
    callbacks = [b.callback_data for row in last_markup.inline_keyboard for b in row if b.callback_data]
    assert any(c.startswith("orgw:ok:") for c in callbacks), "the approve button must be on the last message"
    # every follow shot keeps its own reject button
    for _, _, _, markup in to_host[:-1]:
        cbs = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
        assert any(c.startswith("socno:") for c in cbs)


@pytest.mark.asyncio
async def test_each_follow_shot_says_which_page_it_is(async_db):
    from app.bot.handlers import winner as winner_panel

    event, host, player, claim = await _claim_setup(async_db, social=True, pages=2)
    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", claim)

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert "instagram.com/p0" in to_host[0][2]
    assert "instagram.com/p1" in to_host[1][2]
    assert "۱ از ۲" in to_host[0][2]
    assert "اسکرین «برنده شدن»" in to_host[-1][2]


@pytest.mark.asyncio
async def test_a_custom_without_a_follow_gate_sends_one_photo(async_db):
    from app.bot.handlers import winner as winner_panel

    event, host, player, claim = await _claim_setup(async_db, social=False)
    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", claim)

    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert len(to_host) == 1
    assert "شرط فالو نداشت" in to_host[0][2]


@pytest.mark.asyncio
async def test_a_missing_follow_screenshot_is_called_out(async_db):
    """The organizer has to be able to see that the "winner" never followed."""
    from sqlalchemy import delete

    from app.bot.handlers import winner as winner_panel

    event, host, player, claim = await _claim_setup(async_db, social=True, pages=2)
    await async_db.execute(delete(SocialProof).where(SocialProof.event_id == event.id))
    await async_db.flush()

    rec = Recorder()
    await winner_panel._notify_winner_claim(rec, async_db, event, player, "win-shot", claim)
    to_host = [p for p in rec.photos if p[0] == host.telegram_id]
    assert len(to_host) == 1
    assert "هیچ اسکرین فالویی ثبت نشده" in to_host[0][2]


# --- publishing the banner into a channel ----------------------------------


class PostRecorder(Recorder):
    def __init__(self, fail=None):
        super().__init__()
        self.fail = fail
        self.views: list = []
        self.alerts: list = []
        self.id = 1

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        if self.fail is not None and chat_id == self.fail:
            raise RuntimeError("no rights")
        self.sent.append((chat_id, text, reply_markup))
        return type("M", (), {"message_id": 77})()

    async def send_photo(self, chat_id, file_id, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, file_id, caption, reply_markup))
        return type("M", (), {"message_id": 77})()


class PostMessage:
    def __init__(self, rec):
        self.rec = rec
        self.bot = rec
        self.text = "x"
        self.caption = None
        self.photo = None
        self.chat = type("C", (), {"id": 999})()

    async def answer(self, text, reply_markup=None, **kw):
        self.rec.views.append((text, reply_markup))

    async def answer_photo(self, file_id, caption=None, reply_markup=None, **kw):
        self.rec.photos.append((None, file_id, caption, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.rec.views.append((text, reply_markup))


class PostCb:
    def __init__(self, data, rec):
        self.data = data
        self.rec = rec
        self.message = PostMessage(rec)
        self.bot = rec
        self.from_user = type("U", (), {"id": 1})()

    async def answer(self, text="", show_alert=False):
        self.rec.alerts.append((text, show_alert))


async def _channel_event(async_db):
    from app.core.security import generate_unguessable_token
    from app.models.channel import Channel
    from app.models.event import Event, EventRequiredChannel

    host = User(telegram_id=7300, first_name="host", username="hostguy")
    async_db.add(host)
    await async_db.flush()
    async_db.add(UserProfile(user_id=host.id))
    org = Organizer(user_id=host.id, status="approved", display_name="Host")
    async_db.add(org)
    await async_db.flush()
    channel = Channel(
        telegram_chat_id=-100999, title="کانال کلن", username="myclan", bot_is_admin=True
    )
    async_db.add(channel)
    await async_db.flush()
    now = datetime.now(UTC)
    event = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        channel_id=channel.id,
        title="Custom",
        starts_at=now + timedelta(hours=3),
        registration_ends_at=now + timedelta(hours=3),
        credentials_send_at=now + timedelta(hours=3),
        capacity=0,
        status=EventStatus.PUBLISHED,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        prize_summary="واریز یک میلیون به کارت\n۵۰۰ هزار تومان",
    )
    async_db.add(event)
    await async_db.flush()
    async_db.add(EventRequiredChannel(event_id=event.id, channel_id=channel.id, is_active=True))
    await async_db.flush()
    await async_db.commit()
    return event, host, channel


@pytest.mark.asyncio
async def test_the_preview_shows_the_organizer_exactly_what_the_channel_gets(async_db):
    from app.bot.handlers import organizer as org_panel

    event, host, channel = await _channel_event(async_db)
    rec = PostRecorder()
    await org_panel.org_post_preview(PostCb(f"orgp:post:{event.public_token}", rec), async_db, host)

    assert not rec.photos, "the post is text, not a picture"
    assert rec.sent, "no preview was sent"
    _, text, markup = rec.sent[-1]
    assert text.startswith("🚨 کاستـوم جایـزه دار")
    assert markup.inline_keyboard[0][0].text == "ورود به کاستوم جایزه دار"
    # and nothing has been posted to the channel yet
    assert all(chat != channel.telegram_chat_id for chat, *_ in rec.sent)
    assert any("مقصد" in view for view, _ in rec.views)


@pytest.mark.asyncio
async def test_publishing_sends_the_same_post_to_the_channel(async_db, monkeypatch):
    from app.bot.handlers import organizer as org_panel

    event, host, channel = await _channel_event(async_db)

    async def _admin(bot, chat_ref):
        return type("R", (), {"is_admin": True})()

    monkeypatch.setattr(org_panel, "inspect_bot_admin", _admin)
    rec = PostRecorder()
    await org_panel.org_post_publish(PostCb(f"orgp:pub:{event.public_token}", rec), async_db, host)

    to_channel = [m for m in rec.sent if m[0] == channel.telegram_chat_id]
    assert len(to_channel) == 1
    _, text, markup = to_channel[0]
    assert not rec.photos, "the channel gets a text post, never an image"
    assert "🥇" in text and "🥈" in text
    assert markup.inline_keyboard[0][0].url.endswith(f"?start=event_{event.public_token}")


@pytest.mark.asyncio
async def test_publishing_refuses_when_the_bot_lost_admin(async_db, monkeypatch):
    """bot_is_admin is written once at connect time and goes stale."""
    from app.bot.handlers import organizer as org_panel

    event, host, channel = await _channel_event(async_db)

    async def _not_admin(bot, chat_ref):
        return type("R", (), {"is_admin": False})()

    monkeypatch.setattr(org_panel, "inspect_bot_admin", _not_admin)
    rec = PostRecorder()
    await org_panel.org_post_publish(PostCb(f"orgp:pub:{event.public_token}", rec), async_db, host)

    assert not [m for m in rec.sent if m[0] == channel.telegram_chat_id]
    assert any("ادمین" in view for view, _ in rec.views)


@pytest.mark.asyncio
async def test_a_send_failure_is_reported_not_swallowed(async_db, monkeypatch):
    from app.bot.handlers import organizer as org_panel

    event, host, channel = await _channel_event(async_db)

    async def _admin(bot, chat_ref):
        return type("R", (), {"is_admin": True})()

    monkeypatch.setattr(org_panel, "inspect_bot_admin", _admin)
    rec = PostRecorder(fail=channel.telegram_chat_id)
    await org_panel.org_post_publish(PostCb(f"orgp:pub:{event.public_token}", rec), async_db, host)

    assert any("انجام نشد" in text for text, _ in rec.views)
