from app.models.channel import Channel
from app.models.event import EventRequiredChannel
from app.services.event_display import (
    default_custom_description,
    event_about_text,
    format_capacity_line,
    format_event_identity_block,
    format_event_list_label,
    format_time_left,
    resolve_event_channel,
)
from tests.conftest import make_event, make_organizer, make_user


def test_event_card_order_helpers(db):
    host = make_user(db, 901, username="ali_host")
    org = make_organizer(db, host)
    org.display_name = "علی کاستوم"
    event = make_event(db, org, title="کاستوم شب", prize_summary="۵۰۰ الماس")
    event.description = "کاستوم کلن ویژه فالوورها"
    ch = Channel(telegram_chat_id=-100123, title="کانال علی", username="alichannel")
    db.add(ch)
    db.flush()
    event.channel_id = ch.id
    event.channel = ch
    event.organizer = org
    db.flush()

    block = format_event_identity_block(event)
    assert "علی کاستوم" in block
    assert "کانال علی" in block
    assert "کاستوم کلن" in block
    assert block.index("برگزارکننده") < block.index("جایزه")
    assert block.index("درباره کاستوم") < block.index("جایزه")
    label = format_event_list_label(event)
    # the prize is the reason anyone taps the button, so it must be in the label
    assert "۵۰۰ الماس" in label
    assert len(label) <= 64


def test_default_custom_description_fallbacks():
    assert default_custom_description(custom_description="توضیح من") == "توضیح من"
    assert default_custom_description(title="کاستوم شب") == "کاستوم شب"
    # nothing worth inventing: the auto title only repeats the channel name
    assert default_custom_description(channel_title="Ali Channel") is None
    assert default_custom_description(title="کاستوم Ali Channel", channel_title="Ali Channel") is None
    assert default_custom_description() is None


def test_resolve_channel_from_required(db):
    host = make_user(db, 903)
    org = make_organizer(db, host)
    event = make_event(db, org)
    ch = Channel(telegram_chat_id=-100999, title="کانال پشتیبان", username="backup")
    db.add(ch)
    db.flush()
    event.channel_id = None
    event.channel = None
    link = EventRequiredChannel(event_id=event.id, channel_id=ch.id, is_active=True)
    link.channel = ch
    event.required_channels = [link]
    assert resolve_event_channel(event).title == "کانال پشتیبان"


def test_event_about_falls_back_to_title(db):
    host = make_user(db, 902)
    org = make_organizer(db, host)
    event = make_event(db, org, title="کاستوم شب جمعه")
    event.description = event.prize_summary
    assert event_about_text(event) == "کاستوم شب جمعه"


def test_about_block_hidden_when_it_only_repeats_the_channel(db):
    host = make_user(db, 904)
    org = make_organizer(db, host)
    event = make_event(db, org, title="کاستوم کانال علی", prize_summary="۱۰۰۰ الماس")
    ch = Channel(telegram_chat_id=-100777, title="کانال علی")
    db.add(ch)
    db.flush()
    event.channel_id = ch.id
    event.channel = ch
    event.description = "کاستوم کانال علی"
    event.organizer = org
    db.flush()

    assert event_about_text(event) == ""
    block = format_event_identity_block(event)
    assert "درباره کاستوم" not in block
    assert "۱۰۰۰ الماس" in block


def test_time_left_is_human_readable():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert format_time_left(now + timedelta(minutes=45), now) == "مانده: 45 دقیقه"
    assert format_time_left(now + timedelta(hours=3, minutes=10), now) == "مانده: 3 ساعت و 10 دقیقه"
    assert format_time_left(now + timedelta(days=2, hours=3), now) == "مانده: 2 روز و 3 ساعت"
    assert format_time_left(now - timedelta(minutes=1), now) == "ساعت کاستوم رسیده"


def test_capacity_line_shows_free_seats(db):
    host = make_user(db, 905)
    org = make_organizer(db, host)
    event = make_event(db, org, capacity=10)
    event.confirmed_count = 4
    assert "6 جای خالی" in format_capacity_line(event)
    event.confirmed_count = 10
    assert "تکمیل" in format_capacity_line(event)


def test_list_label_stays_inside_the_button_limit(db):
    host = make_user(db, 906)
    org = make_organizer(db, host)
    org.display_name = "برگزارکننده با نام خیلی خیلی طولانی"
    event = make_event(
        db,
        org,
        prize_summary="۱۰۰۰ الماس + اسکین ویژه + کارت هدیه ۵۰۰ هزار تومانی برای نفر اول",
    )
    event.organizer = org
    label = format_event_list_label(event)
    assert len(label) <= 64
    assert "الماس" in label
