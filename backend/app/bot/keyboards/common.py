from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

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
    rows = [
        [kbtn("کاستوم‌های جایزه‌دار", SUCCESS), kbtn("کاستوم‌های امروز", PRIMARY)],
        [kbtn("ثبت کاستوم", SUCCESS), kbtn("پنل برگزارکننده", PRIMARY)],
        [kbtn("ثبت‌نام‌های من", PRIMARY), kbtn("دعوت دوستان", SUCCESS)],
        [kbtn("راهنما و قوانین", PRIMARY), kbtn("پروفایل", PRIMARY)],
        [kbtn("پشتیبانی", SUCCESS), kbtn("نتایج و تاریخچه", PRIMARY)],
        [kbtn("اعلان‌های من", PRIMARY), kbtn("اطلاع‌رسانی", SUCCESS)],
        [kbtn("شروع مجدد", DANGER)],
    ]
    if admin:
        rows.append([kbtn("پنل مالک ربات", PRIMARY)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="کاستوم جایزه‌دار Free Fire",
    )


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
    back: str = "upcoming",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    seen: set[str] = set()
    for title, url in join_urls or []:
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append([ibtn(f"عضویت در {title[:28]}", url=url, style=PRIMARY)])
    if can_join:
        rows.append([ibtn("عضو شدم — بررسی و ثبت‌نام", callback_data=f"join:{token}", style=SUCCESS)])
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
    for item in upcoming_local_dates(3):
        style = SUCCESS if item["offset"] == 0 else PRIMARY
        rows.append([ibtn(item["label"], callback_data=f"{prefix}:{item['offset']}", style=style)])
    rows.append([ibtn("لغو", callback_data="wiz:cancel", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wizard_nav(include_skip: bool = False) -> InlineKeyboardMarkup:
    row = [ibtn("لغو", callback_data="wiz:cancel", style=DANGER)]
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
    return share_link_kb(link, open_label="ورود به کاستوم از لینک", copy_label="کپی لینک بنر")


def organizer_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("ثبت کاستوم جدید", callback_data="orgp:new", style=SUCCESS)],
            [ibtn("کاستوم‌ها و آمار من", callback_data="orgp:mine", style=PRIMARY)],
            [ibtn("کانال‌های من", callback_data="orgp:ch", style=PRIMARY)],
            [ibtn("راهنمای برگزارکننده", callback_data="help:host", style=PRIMARY)],
            [ibtn("منوی اصلی", callback_data="menu:home", style=DANGER)],
        ]
    )


def send_creds_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ibtn("ارسال ROOM ID", callback_data=f"orgp:creds:{token}", style=SUCCESS)]]
    )


def announcement_list_kb(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[ibtn(title[:60], callback_data=f"annv:{aid}", style=PRIMARY)] for aid, title in items]
    rows.append([ibtn("ثبت اطلاع‌رسانی", callback_data="ann:new", style=SUCCESS)])
    rows.append([ibtn("بازگشت", callback_data="menu:home", style=DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
