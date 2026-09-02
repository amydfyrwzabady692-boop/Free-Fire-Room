"""Paged inline lists for the Telegram panels.

Every admin section used to answer a tap by sending 10-20 separate messages in
a tight loop. Telegram throttles at roughly one message per second per chat, so
the tail of a long list arrives late, out of order, or not at all - and the
owner has no way back to the top.

A paged view is one message that gets edited in place instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.common import DANGER, PRIMARY, SUCCESS, ibtn

DEFAULT_PAGE_SIZE = 5


@dataclass(frozen=True)
class Page:
    items: list
    index: int
    pages: int
    total: int

    @property
    def first(self) -> int:
        """1-based index of the first item on this page."""
        return 0 if not self.total else self.index * DEFAULT_PAGE_SIZE + 1

    @property
    def last(self) -> int:
        return min(self.total, self.first + len(self.items) - 1)


def paginate(rows: Sequence, index: int, size: int = DEFAULT_PAGE_SIZE) -> Page:
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    index = max(0, min(index, pages - 1))
    start = index * size
    return Page(items=list(rows[start : start + size]), index=index, pages=pages, total=total)


def nav_row(prefix: str, page: Page) -> list[InlineKeyboardButton]:
    """Prev / counter / next. The counter is a no-op button, not a dead end."""
    if page.pages <= 1:
        return []
    row = []
    if page.index > 0:
        row.append(ibtn("قبلی", callback_data=f"{prefix}:{page.index - 1}", style=PRIMARY))
    row.append(ibtn(f"{page.index + 1}/{page.pages}", callback_data="nav:noop", style=PRIMARY))
    if page.index < page.pages - 1:
        row.append(ibtn("بعدی", callback_data=f"{prefix}:{page.index + 1}", style=PRIMARY))
    return row


def paged_kb(
    prefix: str,
    page: Page,
    *,
    item_button: Callable[[object], InlineKeyboardButton] | None = None,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    back: str | None = None,
    back_label: str = "بازگشت",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if item_button is not None:
        for item in page.items:
            button = item_button(item)
            if button is not None:
                rows.append([button])
    for extra in extra_rows or []:
        rows.append(extra)
    nav = nav_row(prefix, page)
    if nav:
        rows.append(nav)
    if back:
        rows.append([ibtn(back_label, callback_data=back, style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def page_header(title: str, page: Page, *, empty: str) -> str:
    if not page.total:
        return f"<b>{title}</b>\n\n{empty}"
    if page.pages == 1:
        return f"<b>{title}</b> ({page.total} مورد)"
    return f"<b>{title}</b> — {page.first} تا {page.last} از {page.total}"


def parse_page(data: str, prefix: str) -> int:
    """Read the page index out of a "<prefix>:<n>" callback payload."""
    if not data.startswith(prefix):
        return 0
    tail = data[len(prefix) :].lstrip(":")
    try:
        return max(0, int(tail))
    except ValueError:
        return 0


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "Page",
    "SUCCESS",
    "page_header",
    "paged_kb",
    "paginate",
    "parse_page",
    "nav_row",
]
