"""Every button a keyboard draws must land on a registered handler.

This is the cheap guard against the failure the user actually reported - a
button that looks fine and does nothing because its callback prefix and its
handler drifted apart.
"""

from __future__ import annotations

import pytest
from aiogram.filters import StateFilter
from aiogram.fsm.state import State
from magic_filter import MagicFilter

from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.announce import router as announce_router
from app.bot.handlers.organizer import router as organizer_router
from app.bot.handlers.player import router as player_router
from app.bot.handlers.winner import router as winner_router
from app.bot.keyboards.common import (
    organizer_profile_kb,
    post_targets_kb,
    social_bulk_kb,
    social_reject_all_kb,
    start_confirm_kb,
    event_detail_kb,
    organizer_home_kb,
    organizer_reply_kb,
    payout_contact_kb,
    social_review_kb,
    social_step_kb,
    winner_claim_review_kb,
    winner_reply_kb,
)

ROUTERS = (admin_router, announce_router, organizer_router, winner_router, player_router)

CLAIM = "11111111-1111-1111-1111-111111111111"
#: a real public_token is secrets.token_urlsafe(18) = 24 characters. The old
#: 6-character stand-in could never catch a callback_data overflow.
TOKEN = "AbCdEfGhIjKlMnOpQrStUvWx"
HEX = "11111111111111111111111111111111"


class FakeCb:
    """Only what a magic filter on F.data ever touches."""

    def __init__(self, data: str):
        self.data = data


def _matches(handler, cb: FakeCb) -> bool:
    for f in handler.filters or []:
        target = getattr(f.callback, "__self__", f.callback)
        # a state condition is satisfied by being in that state; routing-wise
        # the data filter is what we are checking here
        if isinstance(target, (StateFilter, State)):
            continue
        if isinstance(target, MagicFilter):
            try:
                if not target.resolve(cb):
                    return False
            except Exception:  # noqa: BLE001 - filter reads a field we do not fake
                return False
            continue
        return False
    return True


def _handled(data: str) -> bool:
    cb = FakeCb(data)
    for router in ROUTERS:
        for handler in router.observers["callback_query"].handlers:
            if _matches(handler, cb):
                return True
    return False


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


class _FakeChannel:
    """Just enough Channel for channel_public_label()."""

    def __init__(self, username: str):
        self.title = "کانال"
        self.username = username


ALL_KEYBOARDS = {
    "organizer_home": organizer_home_kb(),
    "payout_saved": payout_contact_kb(saved="@old", username="me"),
    "social_step": social_step_kb(TOKEN, "https://instagram.com/p"),
    "social_review": social_review_kb(CLAIM),
    "social_review_rejected": social_review_kb(
        CLAIM, status="rejected", back=f"orgp:soc:{TOKEN}"
    ),
    "social_reject_all": social_reject_all_kb(TOKEN, 12),
    "post_targets_one": post_targets_kb(TOKEN, [_FakeChannel("@one")]),
    "post_targets_many": post_targets_kb(
        TOKEN, [_FakeChannel("@one"), _FakeChannel("@two"), _FakeChannel("@three")]
    ),
    "winner_claim_review": winner_claim_review_kb(CLAIM),
    "winner_claim_reviewed": winner_claim_review_kb(CLAIM, approved=True),
    "winner_reply": winner_reply_kb(CLAIM, contact_url="https://t.me/x"),
    "organizer_reply": organizer_reply_kb(CLAIM, player_url="https://t.me/y"),
    "social_bulk": social_bulk_kb(TOKEN, 3),
    "organizer_profile": organizer_profile_kb(
        CLAIM, [(TOKEN, "کاستوم")], share_link="https://t.me/x"
    ),
    "start_confirm": start_confirm_kb(TOKEN),
    "event_detail_with_social": event_detail_kb(
        TOKEN,
        can_join=True,
        can_claim_win=True,
        can_review=True,
        show_reviews=True,
        social_url="https://instagram.com/p",
        channels_done=True,
    ),
    "event_detail_after_the_custom": event_detail_kb(
        TOKEN,
        can_claim_win=True,
        can_review=True,
        show_reviews=True,
        organizer_id=CLAIM,
        show_aftercare=True,
    ),
}


def test_the_working_card_stays_short():
    """The card a player sees mid-conditions must not turn into a wall."""
    working = event_detail_kb(
        TOKEN,
        join_urls=[("کانال یک", "https://t.me/a"), ("کانال دو", "https://t.me/b")],
        can_join=True,
        social_url="https://instagram.com/p",
        organizer_id=CLAIM,
        can_review=True,
        show_reviews=True,
    )
    labels = [b.text for row in working.inline_keyboard for b in row]
    assert not any("گزارش" in text for text in labels), "reporting is after-the-fact"
    assert not any("برگزارکننده" in text for text in labels)
    assert not any("نظر" in text for text in labels)
    # the follow step is hidden until the channels are green
    assert not any("اسکرین" in text for text in labels)
    assert len(labels) <= 4, labels


def test_the_follow_step_only_appears_after_the_channels():
    before = event_detail_kb(TOKEN, can_join=True, social_url="https://instagram.com/p")
    after = event_detail_kb(
        TOKEN, can_join=True, social_url="https://instagram.com/p", channels_done=True
    )
    assert f"soc:{TOKEN}" not in _callbacks(before)
    assert f"soc:{TOKEN}" in _callbacks(after)
    # and it comes after "عضو شدم", never before it
    order = [b.text for row in after.inline_keyboard for b in row]
    assert order.index("عضو شدم — بررسی و ثبت‌نام") < order.index("ارسال اسکرین‌شات فالو")


def test_no_callback_outgrows_the_64_byte_limit():
    """Telegram rejects callback_data over 64 BYTES, silently in most clients."""
    too_long = []
    for name, markup in ALL_KEYBOARDS.items():
        for data in _callbacks(markup):
            if len(data.encode("utf-8")) > 64:
                too_long.append((name, data, len(data.encode("utf-8"))))
    assert not too_long, too_long


def test_the_channel_button_says_exactly_what_the_owner_asked():
    from app.bot.keyboards.common import CHANNEL_POST_LABEL, channel_post_kb

    kb = channel_post_kb("https://t.me/bot?start=event_abc")
    button = kb.inline_keyboard[0][0]
    assert button.text == "ورود به کاستوم جایزه دار"
    assert button.text == CHANNEL_POST_LABEL
    assert button.url.startswith("https://t.me/")
    assert button.callback_data is None, "a channel post must not need the bot to answer a tap"


@pytest.mark.parametrize("name", sorted(ALL_KEYBOARDS))
def test_every_button_reaches_a_handler(name):
    unrouted = [data for data in _callbacks(ALL_KEYBOARDS[name]) if not _handled(data)]
    assert not unrouted, f"{name}: no handler for {unrouted}"


def test_the_check_can_actually_fail():
    """Otherwise a broken _matches would make the whole file pass vacuously."""
    assert _handled("this:prefix:does:not:exist") is False


@pytest.mark.parametrize(
    "data",
    [
        f"orgp:start:{TOKEN}",
        f"orgp:soc:{TOKEN}",
        "orgp:win",
        "orgp:payout",
        f"socok:{CLAIM}",
        f"socno:{CLAIM}",
        f"orgw:ok:{CLAIM}",
        f"orgw:no:{CLAIM}",
        f"orgw:msg:{CLAIM}",
        f"winr:{CLAIM}",
        f"soc:{TOKEN}",
        "payc:saved",
        "payc:self",
        "orgp:startmenu",
        f"orgp:startpick:{TOKEN}",
        f"socall:ok:{TOKEN}",
        f"socall:no:{TOKEN}",
        "socdone",
        f"orgp:rep:{TOKEN}",
        "repd:0",
        "orgp:me",
        f"orgprof:{CLAIM}",
        f"orgrev:{CLAIM}",
        f"socv:{HEX}",
        f"socall:ask:{TOKEN}",
        f"orgp:soc:{TOKEN}:2:r",
        f"orgp:post:{TOKEN}",
        f"orgp:pub:{TOKEN}:0",
        f"orgp:pub:{TOKEN}:a",
    ],
)
def test_new_callbacks_are_routed(data):
    assert _handled(data), f"nothing handles {data}"
