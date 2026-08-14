from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu(*, admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="کاستوم‌های آینده"), KeyboardButton(text="کاستوم‌های امروز")],
        [KeyboardButton(text="ثبت‌نام‌های من"), KeyboardButton(text="نتایج و تاریخچه")],
        [KeyboardButton(text="دعوت دوستان"), KeyboardButton(text="ثبت کاستوم")],
        [KeyboardButton(text="اعلان‌های من"), KeyboardButton(text="پروفایل")],
        [KeyboardButton(text="راهنما و قوانین"), KeyboardButton(text="پشتیبانی")],
    ]
    if admin:
        rows.append([KeyboardButton(text="پنل ادمین")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def tos_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="می‌پذیرم", callback_data="tos:accept")],
            [InlineKeyboardButton(text="سیاست حریم خصوصی", callback_data="tos:privacy")],
        ]
    )


def membership_kb(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, url=url)] for title, url in buttons if url]
    rows.append([InlineKeyboardButton(text="بررسی مجدد عضویت", callback_data="membership:recheck")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_list_kb(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title[:60], callback_data=f"ev:{token}")] for token, title in items]
    rows.append([InlineKeyboardButton(text="بازگشت", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_detail_kb(token: str, can_join: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_join:
        rows.append([InlineKeyboardButton(text="شرکت می‌کنم", callback_data=f"join:{token}")])
    rows.append([InlineKeyboardButton(text="بررسی مجدد شرایط", callback_data=f"req:{token}")])
    rows.append([InlineKeyboardButton(text="دعوت دوستان", callback_data=f"inv:{token}")])
    rows.append([InlineKeyboardButton(text="گزارش تخلف", callback_data=f"rep:{token}")])
    rows.append([InlineKeyboardButton(text="بازگشت به فهرست", callback_data="list:upcoming")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def checklist_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="پذیرش قوانین", callback_data=f"rules:{token}")],
            [InlineKeyboardButton(text="بررسی مجدد شرایط", callback_data=f"req:{token}")],
            [InlineKeyboardButton(text="دعوت دوستان", callback_data=f"inv:{token}")],
            [InlineKeyboardButton(text="بازگشت", callback_data=f"ev:{token}")],
        ]
    )


def wizard_nav(include_skip: bool = False) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="لغو", callback_data="wiz:cancel")]
    if include_skip:
        row.insert(0, InlineKeyboardButton(text="رد کردن", callback_data="wiz:skip"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="تأیید", callback_data=f"ok:{action}"),
                InlineKeyboardButton(text="انصراف", callback_data="menu:home"),
            ]
        ]
    )
