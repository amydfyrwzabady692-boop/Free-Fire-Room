"""The admin panel used to answer one tap with 10-20 separate messages, which
Telegram throttles per chat. Everything is paged into one edited message now."""

from app.bot.paging import DEFAULT_PAGE_SIZE, page_header, paged_kb, paginate, parse_page


def test_paginate_splits_and_clamps():
    rows = list(range(12))
    first = paginate(rows, 0)
    assert first.items == [0, 1, 2, 3, 4]
    assert (first.index, first.pages, first.total) == (0, 3, 12)
    assert (first.first, first.last) == (1, 5)

    last = paginate(rows, 2)
    assert last.items == [10, 11]
    assert (last.first, last.last) == (11, 12)

    # a stale button from an older, longer list must not explode
    assert paginate(rows, 99).index == 2
    assert paginate(rows, -5).index == 0


def test_paginate_empty():
    page = paginate([], 3)
    assert page.items == []
    assert page.total == 0
    assert page.pages == 1
    assert page.first == 0


def test_parse_page_survives_junk():
    assert parse_page("adm:ev:2", "adm:ev") == 2
    assert parse_page("adm:ev", "adm:ev") == 0
    assert parse_page("adm:ev:not-a-number", "adm:ev") == 0
    assert parse_page("adm:ev:-4", "adm:ev") == 0
    assert parse_page("something:else", "adm:ev") == 0


def test_nav_row_only_offers_reachable_pages():
    rows = list(range(12))
    kb = paged_kb("adm:ev", paginate(rows, 0), back="adm:home")
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "adm:ev:1" in data
    assert "adm:ev:-1" not in data
    assert "adm:home" in data

    kb_last = paged_kb("adm:ev", paginate(rows, 2), back="adm:home")
    data_last = [b.callback_data for row in kb_last.inline_keyboard for b in row]
    assert "adm:ev:1" in data_last
    assert "adm:ev:3" not in data_last


def test_single_page_has_no_pager():
    kb = paged_kb("adm:ev", paginate([1, 2], 0), back="adm:home")
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["adm:home"]


def test_page_header_says_where_you_are():
    rows = list(range(12))
    assert "1 تا 5 از 12" in page_header("عنوان", paginate(rows, 0), empty="")
    assert "هیچی" in page_header("عنوان", paginate([], 0), empty="هیچی")
    assert "(2 مورد)" in page_header("عنوان", paginate([1, 2], 0), empty="")


def test_item_buttons_are_one_per_row():
    from app.bot.keyboards.common import ibtn

    page = paginate(list(range(DEFAULT_PAGE_SIZE)), 0)
    kb = paged_kb("p", page, item_button=lambda i: ibtn(f"item {i}", callback_data=f"x:{i}"))
    item_rows = [r for r in kb.inline_keyboard if r and r[0].callback_data.startswith("x:")]
    assert len(item_rows) == DEFAULT_PAGE_SIZE
    assert all(len(r) == 1 for r in item_rows)
