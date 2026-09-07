from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.bot.helpers import add_bot_to_channel_url

try:
    from aiogram.enums import ButtonStyle

    PRIMARY = ButtonStyle.PRIMARY
    SUCCESS = ButtonStyle.SUCCESS
    DANGER = ButtonStyle.DANGER
except Exception:  # pragma: no cover
    PRIMARY = "primary"
    SUCCESS = "success"
    DANGER = "danger"

_MARK = {str(SUCCESS): "🟢", str(PRIMARY): "🔵", str(DANGER): "🔴", "success": "🟢", "primary": "🔵", "danger": "🔴"}
_ICON_MARKS = ("🟢", "🔵", "🔴")


def unpaint(text: str) -> str:
    raw = (text or "").strip()
    for mark in _ICON_MARKS:
        if raw.startswith(mark):
            return raw[len(mark) :].lstrip(" \u00a0")
    return raw


def _style_candidates(style) -> list:
    out = []
    for item in (style, getattr(style, "value", None)):
        if item is not None and item not in out:
            out.append(item)
    name = str(style).rsplit(".", 1)[-1].lower()
    if name and name not in {str(x).lower() for x in out}:
        out.append(name)
    return out

_MENU_LABELS = (
    "کاستوم‌های جایزه‌دار",
    "کاستوم‌های آینده",
    "کاستوم‌های امروز",
    "ثبت کاستوم",
    "ثبت کاستوم جایزه‌دار",
    "پنل برگزارکننده",
    "پنل برگزار کننده",
    "کاستوم‌های من",
    "ثبت‌نام‌های من",
    "راهنما و قوانین",
    "راهنما",
    "قوانین",
    "ری‌استارت",
    "ری استارت",
    "پروفایل",
    "پشتیبانی",
    "شروع مجدد",
    "پنل مالک ربات",
    "پنل ادمین",
    "دعوت دوستان",
    "نتایج و تاریخچه",
    "اعلان‌های من",
    "اطلاع‌رسانی",
    "ثبت اطلاع‌رسانی",
    "برنده",
    "برنده شدم",
    "بستن منو",
)


def labeled(*items: str) -> set[str]:
    out: set[str] = set()
    for item in items:
        clean = unpaint(item)
        out.add(clean)
        out.add(item)
        for mark in _ICON_MARKS:
            out.add(f"{mark} {clean}")
            out.add(f"{mark}{clean}")
    return out


MENU_BUTTON_TEXTS = labeled(*_MENU_LABELS)


def _paint(text: str, style) -> str:
    raw = unpaint(text)
    mark = _MARK.get(str(style), "🔵")
    if str(style).lower().endswith("success") or str(style) == str(SUCCESS):
        mark = "🟢"
    elif str(style).lower().endswith("danger") or str(style) == str(DANGER):
        mark = "🔴"
    elif str(style).lower().endswith("primary") or str(style) == str(PRIMARY):
        mark = "🔵"
    return f"{mark} {raw}"[:64]


def ibtn(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    copy_text: str | None = None,
    style=None,
) -> InlineKeyboardButton:
    style = style or PRIMARY
    raw = unpaint(text)[:64]
    extra: dict = {}
    if callback_data:
        extra["callback_data"] = callback_data
    if url:
        extra["url"] = url
    if copy_text:
        from aiogram.types import CopyTextButton

        extra["copy_text"] = CopyTextButton(text=copy_text)
    for candidate in _style_candidates(style):
        try:
            return InlineKeyboardButton(text=raw, style=candidate, **extra)
        except Exception:
            continue
    extra.pop("copy_text", None)
    for candidate in _style_candidates(style):
        try:
            return InlineKeyboardButton(text=raw, style=candidate, **extra)
        except Exception:
            continue
    return InlineKeyboardButton(text=_paint(raw, style), **extra)


def kbtn(text: str, style=None) -> KeyboardButton:
    style = style or PRIMARY
    raw = unpaint(text)[:64]
    for candidate in _style_candidates(style):
        try:
            return KeyboardButton(text=raw, style=candidate)
        except Exception:
            continue
    return KeyboardButton(text=_paint(raw, style))


def main_menu(*, admin: bool = False) -> ReplyKeyboardMarkup:
    _ = admin
    rows = [
        [kbtn("کاستوم‌های جایزه‌دار", SUCCESS), kbtn("ثبت کاستوم", SUCCESS)],
        [kbtn("قوانین", PRIMARY), kbtn("اطلاع‌رسانی", SUCCESS)],
        [kbtn("برنده", SUCCESS), kbtn("پنل برگزارکننده", PRIMARY)],
        [kbtn("دعوت دوستان", SUCCESS), kbtn("پشتیبانی", PRIMARY)],
    ]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=False,
        one_time_keyboard=False,
        input_field_placeholder="کاستوم جایزه‌دار Free Fire",
    )


def hide_menu_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ibtn("بازگشت به منو", callback_data="menu:home", style=DANGER)]]
    )


def tos_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("می‌پذیرم", callback_data="tos:accept", style=SUCCESS)],
            [ibtn("سیاست حریم خصوصی", callback_data="tos:privacy", style=PRIMARY)],
        ]
    )


def membership_kb(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[ibtn(title, url=url, style=PRIMARY)] for title, url in buttons if url]
    rows.append([ibtn("بررسی مجدد عضویت", callback_data="membership:recheck", style=SUCCESS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_list_kb(items: list[tuple[str, str]], *, mode: str | None = None) -> InlineKeyboardMarkup:
    from app.core.config import get_settings

    hours = get_settings().past_events_hours
    rows = []
    for i, (token, title) in enumerate(items):
        rows.append([ibtn(title, callback_data=f"ev:{token}", style=SUCCESS if i == 0 else PRIMARY)])
    if mode == "upcoming":
        rows.append([ibtn("کاستوم‌های امروز", callback_data="list:today", style=SUCCESS)])
        rows.append([ibtn(f"کاستوم‌های {hours} ساعت گذشته", callback_data="list:past", style=PRIMARY)])
    elif mode == "past":
        rows.append([ibtn("کاستوم‌های پیش‌رو", callback_data="list:upcoming", style=SUCCESS)])
    elif mode == "today":
        rows.append([ibtn("همه کاستوم‌های پیش‌رو", callback_data="list:upcoming", style=SUCCESS)])
    elif mode == "mine":
        rows.append([ibtn("کاستوم‌های پیش‌رو", callback_data="list:upcoming", style=SUCCESS)])
    elif mode == "digest":
        rows.append([ibtn("همه کاستوم‌های جایزه‌دار", callback_data="list:upcoming", style=SUCCESS)])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    rows.append([ibtn("بازگشت", callback_data="menu:home", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_detail_kb(
    token: str,
    join_urls: list[tuple[str, str]] | None = None,
    can_join: bool = True,
    can_review: bool = False,
    show_reviews: bool = False,
    can_claim_win: bool = False,
    social_url: str | None = None,
    channels_done: bool = False,
    organizer_id: str | None = None,
    show_aftercare: bool = False,
    back: str = "upcoming",
) -> InlineKeyboardMarkup:
    """One card, one job at a time.

    While the player is still completing conditions the card shows only what
    they have to do next: the channels, then - once every channel is green -
    the follow pages. Reporting, the organizer's profile and the reviews are
    after-the-fact and stay hidden behind ``show_aftercare`` so the working
    card never grows past a handful of buttons.
    """
    rows: list[list[InlineKeyboardButton]] = []
    seen: set[str] = set()
    for title, url in join_urls or []:
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append([ibtn(f"عضویت در {title[:28]}", url=url, style=PRIMARY)])
    if can_join:
        rows.append([ibtn("عضو شدم — بررسی و ثبت‌نام", callback_data=f"join:{token}", style=SUCCESS)])
    # the follow step is the step AFTER the channels, never beside them
    if social_url and channels_done:
        rows.append([ibtn("باز کردن پیج و فالو", url=social_url, style=PRIMARY)])
        rows.append([ibtn("ارسال اسکرین‌شات فالو", callback_data=f"soc:{token}", style=SUCCESS)])
    if can_claim_win:
        rows.append([ibtn("برنده شدم", callback_data=f"win:{token}", style=SUCCESS)])
    if show_aftercare:
        if organizer_id:
            rows.append(
                [ibtn("دربارهٔ برگزارکننده", callback_data=f"orgprof:{organizer_id}", style=PRIMARY)]
            )
        if can_review:
            rows.append([ibtn("نظر و امتیاز", callback_data=f"rev:{token}", style=PRIMARY)])
        if show_reviews:
            rows.append([ibtn("نظرات بازیکن‌ها", callback_data=f"rvl:{token}", style=PRIMARY)])
        rows.append([ibtn("گزارش به مالک ربات", callback_data=f"rep:{token}", style=DANGER)])
    if back not in {"upcoming", "today", "past"}:
        back = "upcoming"
    rows.append([ibtn("بازگشت به فهرست", callback_data=f"list:{back}", style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_stars_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ibtn("⭐ ۱", callback_data=f"rvs:{token}:1", style=DANGER),
                ibtn("⭐⭐ ۲", callback_data=f"rvs:{token}:2", style=DANGER),
                ibtn("⭐⭐⭐ ۳", callback_data=f"rvs:{token}:3", style=PRIMARY),
            ],
            [
                ibtn("⭐⭐⭐⭐ ۴", callback_data=f"rvs:{token}:4", style=PRIMARY),
                ibtn("⭐⭐⭐⭐⭐ ۵", callback_data=f"rvs:{token}:5", style=SUCCESS),
            ],
            [ibtn("بازگشت", callback_data=f"ev:{token}", style=PRIMARY)],
        ]
    )


def review_prize_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("داد — فقط به مالک", callback_data=f"rvp:{token}:yes", style=SUCCESS)],
            [ibtn("نداد — گزارش به مالک", callback_data=f"rvp:{token}:no", style=DANGER)],
            [ibtn("نمی‌دانم", callback_data=f"rvp:{token}:unknown", style=PRIMARY)],
            [ibtn("بازگشت", callback_data=f"rev:{token}", style=PRIMARY)],
        ]
    )


def review_comment_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("بدون توضیح ثبت شود", callback_data=f"rvn:{token}", style=SUCCESS)],
            [ibtn("انصراف", callback_data=f"ev:{token}", style=DANGER)],
        ]
    )


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("ربات چیست؟", callback_data="help:about", style=PRIMARY)],
            [ibtn("شرکت در کاستوم", callback_data="help:play", style=SUCCESS)],
            [ibtn("ثبت کاستوم جایزه‌دار", callback_data="help:host", style=PRIMARY)],
            [ibtn("قوانین، گزارش و امتیاز", callback_data="help:rules", style=DANGER)],
            [ibtn("دو پنل: مالک و برگزارکننده", callback_data="help:panels", style=PRIMARY)],
            [ibtn("سؤالات رایج", callback_data="help:faq", style=PRIMARY)],
            [ibtn("بازگشت به منو", callback_data="menu:home", style=DANGER)],
        ]
    )


def help_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ibtn("بازگشت به راهنما", callback_data="help:home", style=PRIMARY)]])


def report_reasons_kb(token: str, *, cheater_only: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if not cheater_only:
        rows.extend(
            [
                [ibtn("ROOM ID / PASS را نفرستاد", callback_data=f"repr:{token}:no_credentials", style=DANGER)],
                [ibtn("بعد از کاستوم جایزه نداد", callback_data=f"repr:{token}:unpaid_prize", style=DANGER)],
                [ibtn("ROOM ID یا PASS اشتباه بود", callback_data=f"repr:{token}:wrong_room", style=DANGER)],
                [ibtn("جایزه دروغ / کاستوم جعلی", callback_data=f"repr:{token}:fake_prize", style=DANGER)],
            ]
        )
    rows.append([ibtn("چیتر در کاستوم", callback_data=f"repr:{token}:cheater", style=DANGER)])
    if not cheater_only:
        rows.append([ibtn("مورد دیگر", callback_data=f"repr:{token}:other", style=PRIMARY)])
    rows.append([ibtn("بازگشت", callback_data=f"ev:{token}", style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def checklist_kb(token: str, join_urls: list[tuple[str, str]] | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    seen: set[str] = set()
    for title, url in join_urls or []:
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append([ibtn(f"عضویت در {title[:28]}", url=url, style=PRIMARY)])
    rows.append([ibtn("عضو شدم — بررسی مجدد", callback_data=f"join:{token}", style=SUCCESS)])
    rows.append([ibtn("بازگشت", callback_data=f"ev:{token}", style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_required_channel_kb(
    owned: list[tuple[str, str]] | None = None,
    *,
    include_done: bool = False,
    cancel: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    add_url = add_bot_to_channel_url()
    if add_url:
        rows.append([ibtn("۱) افزودن ربات به کانال", url=add_url, style=SUCCESS)])
    for cid, title in owned or []:
        rows.append([ibtn(f"استفاده از {title[:28]}", callback_data=f"chpick:{cid}", style=PRIMARY)])
    if include_done:
        rows.append([ibtn("تمام شد — ادامه", callback_data="chdone", style=SUCCESS)])
    if cancel:
        rows.append([ibtn("لغو", callback_data="wiz:cancel", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pick_date_kb(prefix: str) -> InlineKeyboardMarkup:
    from app.core.time import upcoming_local_dates

    rows = []
    for item in upcoming_local_dates(5):
        style = SUCCESS if item["offset"] == 0 else PRIMARY
        rows.append([ibtn(item["label"], callback_data=f"{prefix}:{item['offset']}", style=style)])
    rows.append([ibtn("لغو", callback_data="wiz:cancel", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wizard_nav(include_skip: bool = False, include_back: bool = False) -> InlineKeyboardMarkup:
    row = [ibtn("لغو", callback_data="wiz:cancel", style=DANGER)]
    if include_back:
        row.insert(0, ibtn("مرحله قبل", callback_data="wiz:back", style=PRIMARY))
    if include_skip:
        row.insert(0, ibtn("رد کردن", callback_data="wiz:skip", style=SUCCESS))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ibtn("تأیید", callback_data=f"ok:{action}", style=SUCCESS),
                ibtn("انصراف", callback_data="menu:home", style=DANGER),
            ]
        ]
    )


def share_link_kb(
    link: str,
    *,
    open_label: str = "باز کردن لینک",
    copy_label: str = "کپی لینک",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn(open_label, url=link, style=SUCCESS)],
            [ibtn(copy_label, copy_text=link, style=PRIMARY)],
        ]
    )


def event_share_kb(link: str) -> InlineKeyboardMarkup:
    return share_link_kb(link, open_label="ورود به کاستوم از لینک", copy_label="کپی لینک کاستوم")


#: The one button that goes out on the channel post. The wording is the
#: organizer's, so it is a constant rather than something a caller passes in.
CHANNEL_POST_LABEL = "ورود به کاستوم جایزه دار"


def channel_post_kb(link: str) -> InlineKeyboardMarkup:
    """The button under the banner in the organizer's own channel.

    Built bare on purpose: ibtn() falls back to prefixing a coloured circle
    when the running aiogram rejects its style kwarg, and this label has to
    reach the channel exactly as written.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=CHANNEL_POST_LABEL, url=link)]]
    )


def organizer_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("ثبت کاستوم جدید", callback_data="orgp:new", style=SUCCESS)],
            [ibtn("ارسال ROOM ID / PASS", callback_data="orgp:creds_menu", style=SUCCESS)],
            [ibtn("⏹ شروع کاستوم (انتقال به گذشته)", callback_data="orgp:startmenu", style=DANGER)],
            [ibtn("کاستوم‌ها و آمار من", callback_data="orgp:mine", style=PRIMARY)],
            [ibtn("برنده‌ها و تحویل جایزه", callback_data="orgp:win", style=PRIMARY)],
            [ibtn("آیدی دریافت جایزه", callback_data="orgp:payout", style=PRIMARY)],
            [ibtn("پروفایل عمومی من", callback_data="orgp:me", style=PRIMARY)],
            [ibtn("کانال‌های من", callback_data="orgp:ch", style=PRIMARY)],
            [ibtn("راهنمای برگزارکننده", callback_data="help:host", style=PRIMARY)],
            [ibtn("منوی اصلی", callback_data="menu:home", style=DANGER)],
        ]
    )


def creds_send_confirm_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("✅ تأیید و ارسال برای واجدین شرایط", callback_data=f"orgp:sendok:{token}", style=SUCCESS)],
            [ibtn("❌ انصراف", callback_data="orgp:sendcancel", style=DANGER)],
        ]
    )


def send_creds_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ibtn("ارسال ROOM ID / PASS", callback_data=f"orgp:creds:{token}", style=SUCCESS)]]
    )


def winner_list_kb(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [ibtn(title, callback_data=f"win:{token}", style=SUCCESS if i == 0 else PRIMARY)]
        for i, (token, title) in enumerate(items)
    ]
    rows.append([ibtn("بازگشت", callback_data="menu:home", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def announcement_list_kb(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[ibtn(title[:60], callback_data=f"annv:{aid}", style=PRIMARY)] for aid, title in items]
    rows.append([ibtn("ثبت اطلاع‌رسانی", callback_data="ann:new", style=SUCCESS)])
    rows.append([ibtn("بازگشت", callback_data="menu:home", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def social_step_kb(token: str, url: str | None) -> InlineKeyboardMarkup:
    """Last gate before a registration is confirmed: follow, then send a shot."""
    rows: list[list[InlineKeyboardButton]] = []
    if url:
        rows.append([ibtn("باز کردن پیج و فالو کردن", url=url, style=PRIMARY)])
    rows.append([ibtn("ارسال اسکرین‌شات فالو", callback_data=f"soc:{token}", style=SUCCESS)])
    rows.append([ibtn("بازگشت به کاستوم", callback_data=f"ev:{token}", style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def social_review_kb(
    proof_id: str, *, status: str | None = None, back: str | None = None
) -> InlineKeyboardMarkup:
    """What the organizer can do to one screenshot.

    There is nothing to approve - the player is registered the moment they send
    it. The only real action is rejecting a screenshot that shows something
    else, and undoing that when it was a mistake.
    """
    from app.core.enums import SocialProofStatus

    rows: list[list[InlineKeyboardButton]] = []
    if status == SocialProofStatus.REJECTED:
        rows.append([ibtn("اشتباه رد شد — قبولش کن", callback_data=f"socok:{proof_id}", style=SUCCESS)])
    else:
        rows.append(
            [ibtn("رد این اسکرین — ورودش باطل شود", callback_data=f"socno:{proof_id}", style=DANGER)]
        )
    if back:
        rows.append([ibtn("بازگشت به فهرست اسکرین‌ها", callback_data=back, style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payout_contact_kb(*, saved: str | None = None, username: str | None = None) -> InlineKeyboardMarkup:
    """One tap for the id this organizer used last time, or their own @username."""
    rows: list[list[InlineKeyboardButton]] = []
    seen: set[str] = set()
    if saved:
        seen.add(saved)
        rows.append([ibtn(f"همان قبلی: {saved[:32]}", callback_data="payc:saved", style=SUCCESS)])
    if username:
        handle = f"@{username.lstrip('@')}"
        if handle not in seen:
            rows.append([ibtn(f"آیدی خودم: {handle[:32]}", callback_data="payc:self", style=PRIMARY)])
    rows.append([ibtn("لغو", callback_data="wiz:cancel", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def winner_claim_review_kb(
    claim_id: str, *, approved: bool = False, player_url: str | None = None
) -> InlineKeyboardMarkup:
    """What the organizer sees under a winner's screenshot."""
    rows: list[list[InlineKeyboardButton]] = []
    if not approved:
        rows.append(
            [
                ibtn("تأیید برنده", callback_data=f"orgw:ok:{claim_id}", style=SUCCESS),
                ibtn("رد", callback_data=f"orgw:no:{claim_id}", style=DANGER),
            ]
        )
    rows.append([ibtn("پیام به برنده", callback_data=f"orgw:msg:{claim_id}", style=PRIMARY)])
    if player_url:
        rows.append([ibtn("رفتن به پی‌وی بازیکن", url=player_url, style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def winner_reply_kb(claim_id: str, *, contact_url: str | None = None) -> InlineKeyboardMarkup:
    """What the winner sees: reply through the bot, or open the DM directly."""
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([ibtn("پاسخ به برگزارکننده", callback_data=f"winr:{claim_id}", style=SUCCESS)])
    if contact_url:
        rows.append([ibtn("رفتن به پی‌وی برگزارکننده", url=contact_url, style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def organizer_reply_kb(claim_id: str, *, player_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([ibtn("پاسخ به برنده", callback_data=f"orgw:msg:{claim_id}", style=SUCCESS)])
    if player_url:
        rows.append([ibtn("رفتن به پی‌وی بازیکن", url=player_url, style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def social_bulk_kb(token: str, pending: int) -> InlineKeyboardMarkup:
    """The two whole-queue actions, one of them deliberately behind a confirm.

    "Mark all as checked" is safe - it changes nothing about who is registered,
    it only clears the unreviewed badge. "Reject all" voids every registration
    in the custom at once, so it never fires straight from a tap.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if pending:
        rows.append(
            [ibtn(f"همه درست بود ({pending})", callback_data=f"socall:ok:{token}", style=SUCCESS)]
        )
    rows.append([ibtn("رد همه", callback_data=f"socall:ask:{token}", style=DANGER)])
    rows.append([ibtn("بازگشت به پنل", callback_data="orgp:home", style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def social_reject_all_kb(token: str, total: int) -> InlineKeyboardMarkup:
    """The confirm screen for the one action that cannot be undone in bulk."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn(f"بله، هر {total} نفر باطل شوند", callback_data=f"socall:no:{token}", style=DANGER)],
            [ibtn("نه، بازگشت", callback_data=f"orgp:soc:{token}", style=PRIMARY)],
        ]
    )


def social_filter_row(token: str, counts: dict, current: str) -> list[InlineKeyboardButton]:
    """All / rejected / not-yet-checked, as one row of toggles."""
    def label(text: str, key: str, n: int) -> InlineKeyboardButton:
        mark = "• " if key == current else ""
        return ibtn(
            f"{mark}{text} ({n})",
            callback_data=f"orgp:soc:{token}:0:{key}",
            style=SUCCESS if key == current else PRIMARY,
        )

    return [
        label("همه", "a", int(counts.get("total", 0))),
        label("رد شده", "r", int(counts.get("rejected", 0))),
        label("بررسی‌نشده", "p", int(counts.get("pending", 0))),
    ]


def start_confirm_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("⏹ بله، کاستوم شروع شد", callback_data=f"orgp:start:{token}", style=DANGER)],
            [ibtn("هنوز نه — بازگشت", callback_data="orgp:startmenu", style=PRIMARY)],
        ]
    )


def organizer_profile_kb(
    organizer_id: str,
    events: list,
    *,
    share_link: str | None = None,
    back: str = "menu:home",
) -> InlineKeyboardMarkup:
    """The profile card's buttons: their open customs first, then sharing."""
    rows: list[list[InlineKeyboardButton]] = []
    for token, label in events:
        rows.append([ibtn(label, callback_data=f"ev:{token}", style=SUCCESS)])
    rows.append([ibtn("نظرات بازیکن‌ها", callback_data=f"orgrev:{organizer_id}", style=PRIMARY)])
    if share_link:
        rows.append([ibtn("کپی لینک پروفایل", copy_text=share_link, style=PRIMARY)])
    rows.append([ibtn("بازگشت", callback_data=back, style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def post_confirm_kb(token: str, channel_label: str) -> InlineKeyboardMarkup:
    """Preview first, publish second - a channel post cannot be taken back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn(f"انتشار در {channel_label[:24]}", callback_data=f"orgp:pub:{token}", style=SUCCESS)],
            [ibtn("فعلاً نه", callback_data="orgp:home", style=PRIMARY)],
        ]
    )
