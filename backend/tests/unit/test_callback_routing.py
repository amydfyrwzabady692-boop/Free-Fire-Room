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
TOKEN = "tok123"


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


ALL_KEYBOARDS = {
    "organizer_home": organizer_home_kb(),
    "payout_saved": payout_contact_kb(saved="@old", username="me"),
    "social_step": social_step_kb(TOKEN, "https://instagram.com/p"),
    "social_review": social_review_kb(CLAIM),
    "winner_claim_review": winner_claim_review_kb(CLAIM),
    "winner_claim_reviewed": winner_claim_review_kb(CLAIM, approved=True),
    "winner_reply": winner_reply_kb(CLAIM, contact_url="https://t.me/x"),
    "organizer_reply": organizer_reply_kb(CLAIM, player_url="https://t.me/y"),
    "event_detail_with_social": event_detail_kb(
        TOKEN,
        can_join=True,
        can_claim_win=True,
        can_review=True,
        show_reviews=True,
        social_url="https://instagram.com/p",
    ),
}


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
    ],
)
def test_new_callbacks_are_routed(data):
    assert _handled(data), f"nothing handles {data}"
