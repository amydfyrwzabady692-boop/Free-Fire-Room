from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.bot.helpers import add_bot_to_channel_url

PRIMARY = "primary"
SUCCESS = "success"
DANGER = "danger"


def ibtn(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    data: dict = {"text": text}
    if callback_data:
        data["callback_data"] = callback_data
    if url:
        data["url"] = url
    if style:
        data["style"] = style
    try:
        return InlineKeyboardButton(**data)
    except Exception:
        data.pop("style", None)
        return InlineKeyboardButton(**data)


def kbtn(text: str, style: str | None = None) -> KeyboardButton:
    try:
        if style:
            return KeyboardButton(text=text, style=style)
        return KeyboardButton(text=text)
    except Exception:
        return KeyboardButton(text=text)


def main_menu(*, admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [kbtn("کاستوم‌های جایزه‌دار", PRIMARY)],
        [kbtn("ثبت کاستوم", SUCCESS), kbtn("پنل برگزارکننده", PRIMARY)],
        [kbtn("ثبت‌نام‌های من"), kbtn("راهنما و قوانین")],
        [kbtn("پروفایل"), kbtn("پشتیبانی")],
    ]
    if admin:
        rows.append([kbtn("پنل مالک ربات", PRIMARY)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def tos_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("می‌پذیرم", callback_data="tos:accept", style=SUCCESS)],
            [ibtn("سیاست حریم خصوصی", callback_data="tos:privacy")],
        ]
    )


def membership_kb(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[ibtn(title, url=url, style=PRIMARY)] for title, url in buttons if url]
    rows.append([ibtn("بررسی مجدد عضویت", callback_data="membership:recheck", style=SUCCESS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_list_kb(items: list[tuple[str, str]], *, mode: str | None = None) -> InlineKeyboardMarkup:
    rows = [[ibtn(title[:60], callback_data=f"ev:{token}", style=PRIMARY)] for token, title in items]
    if mode == "upcoming":
        rows.append([ibtn("کاستوم‌های ۴۸ ساعت گذشته", callback_data="list:past")])
    elif mode == "past":
        rows.append([ibtn("کاستوم‌های پیش‌رو", callback_data="list:upcoming", style=PRIMARY)])
    elif mode == "digest":
        rows.append([ibtn("همه کاستوم‌های جایزه‌دار", callback_data="list:upcoming", style=SUCCESS)])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    rows.append([ibtn("بازگشت", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_detail_kb(
    token: str,
    join_urls: list[tuple[str, str]] | None = None,
    can_join: bool = True,
    can_review: bool = False,
    show_reviews: bool = False,
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
        rows.append([ibtn("نظرات بازیکن‌ها", callback_data=f"rvl:{token}")])
    rows.append([ibtn("گزارش به مالک ربات", callback_data=f"rep:{token}", style=DANGER)])
    rows.append([ibtn("بازگشت به فهرست", callback_data="list:upcoming")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_stars_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ibtn("⭐ ۱", callback_data=f"rvs:{token}:1"),
                ibtn("⭐⭐ ۲", callback_data=f"rvs:{token}:2"),
                ibtn("⭐⭐⭐ ۳", callback_data=f"rvs:{token}:3"),
            ],
            [
                ibtn("⭐⭐⭐⭐ ۴", callback_data=f"rvs:{token}:4", style=PRIMARY),
                ibtn("⭐⭐⭐⭐⭐ ۵", callback_data=f"rvs:{token}:5", style=SUCCESS),
            ],
            [ibtn("بازگشت", callback_data=f"ev:{token}")],
        ]
    )


def review_prize_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("داد — فقط به مالک", callback_data=f"rvp:{token}:yes", style=SUCCESS)],
            [ibtn("نداد — گزارش به مالک", callback_data=f"rvp:{token}:no", style=DANGER)],
            [ibtn("نمی‌دانم", callback_data=f"rvp:{token}:unknown")],
            [ibtn("بازگشت", callback_data=f"rev:{token}")],
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
            [ibtn("قوانین، گزارش و امتیاز", callback_data="help:rules")],
            [ibtn("دو پنل: مالک و برگزارکننده", callback_data="help:panels")],
            [ibtn("سؤالات رایج", callback_data="help:faq")],
        ]
    )


def help_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ibtn("بازگشت به راهنما", callback_data="help:home", style=PRIMARY)]])


def report_reasons_kb(token: str, *, cheater_only: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if not cheater_only:
        rows.extend(
            [
                [ibtn("آیدی و رمز را نفرستاد", callback_data=f"repr:{token}:no_credentials", style=DANGER)],
                [ibtn("بعد از کاستوم جایزه نداد", callback_data=f"repr:{token}:unpaid_prize", style=DANGER)],
                [ibtn("رمز یا اتاق اشتباه بود", callback_data=f"repr:{token}:wrong_room", style=DANGER)],
                [ibtn("جایزه دروغ / کاستوم جعلی", callback_data=f"repr:{token}:fake_prize", style=DANGER)],
            ]
        )
    rows.append([ibtn("چیتر در کاستوم", callback_data=f"repr:{token}:cheater", style=DANGER)])
    if not cheater_only:
        rows.append([ibtn("مورد دیگر", callback_data=f"repr:{token}:other")])
    rows.append([ibtn("بازگشت", callback_data=f"ev:{token}")])
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
    rows.append([ibtn("بازگشت", callback_data=f"ev:{token}")])
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
        row.insert(0, ibtn("رد کردن", callback_data="wiz:skip"))
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


def organizer_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("ثبت کاستوم جدید", callback_data="orgp:new", style=SUCCESS)],
            [ibtn("کاستوم‌ها و آمار من", callback_data="orgp:mine", style=PRIMARY)],
            [ibtn("کانال‌های من", callback_data="orgp:ch", style=PRIMARY)],
            [ibtn("راهنمای برگزارکننده", callback_data="help:host")],
            [ibtn("منوی اصلی", callback_data="menu:home")],
        ]
    )


def send_creds_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ibtn("ارسال آیدی و رمز همین حالا", callback_data=f"orgp:creds:{token}", style=SUCCESS)]]
    )


def announcement_list_kb(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[ibtn(title[:60], callback_data=f"annv:{aid}", style=PRIMARY)] for aid, title in items]
    rows.append([ibtn("ثبت اطلاع‌رسانی", callback_data="ann:new", style=SUCCESS)])
    rows.append([ibtn("بازگشت", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
