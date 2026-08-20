from app.core.enums import BanScope, EventStatus, OrganizerStatus, RegistrationStatus

EVENT_STATUS_FA = {
    EventStatus.DRAFT: "پیش‌نویس",
    EventStatus.PENDING_APPROVAL: "در انتظار تأیید",
    EventStatus.PUBLISHED: "منتشرشده",
    EventStatus.FULL: "ظرفیت تکمیل",
    EventStatus.STARTED: "شروع‌شده",
    EventStatus.FINISHED: "تمام‌شده",
    EventStatus.CANCELLED: "لغو شده",
    EventStatus.REJECTED: "رد شده",
}

REG_STATUS_FA = {
    RegistrationStatus.PENDING: "در انتظار جوین",
    RegistrationStatus.CONFIRMED: "ثبت‌نام قطعی",
    RegistrationStatus.WAITLISTED: "لیست انتظار",
    RegistrationStatus.CANCELLED: "لغو شده",
    RegistrationStatus.INELIGIBLE: "واجد شرایط نیست",
}

ORG_STATUS_FA = {
    OrganizerStatus.PENDING: "در انتظار تأیید",
    OrganizerStatus.APPROVED: "تأیید شده",
    OrganizerStatus.REJECTED: "رد شده",
    OrganizerStatus.SUSPENDED: "معلق",
}

BAN_SCOPE_FA = {
    BanScope.BOT: "کل ربات",
    BanScope.ORGANIZE: "برگزاری کاستوم",
    BanScope.PARTICIPATE: "شرکت در کاستوم",
}

SETTING_FA = {
    "event_approval_required": "تأیید کاستوم قبل از انتشار",
    "auto_approve_organizers": "تأیید خودکار برگزارکننده",
    "maintenance_mode": "حالت تعمیرات",
}


def fa_label(value, mapping: dict, fallback: str | None = None) -> str:
    key = str(value or "")
    if value in mapping:
        return mapping[value]
    if key in mapping:
        return mapping[key]
    return fallback if fallback is not None else key


def event_status_fa(value) -> str:
    return fa_label(value, EVENT_STATUS_FA)


def reg_status_fa(value) -> str:
    return fa_label(value, REG_STATUS_FA)


def org_status_fa(value) -> str:
    return fa_label(value, ORG_STATUS_FA)


def ban_scope_fa(value) -> str:
    return fa_label(value, BAN_SCOPE_FA)


def setting_fa(key: str) -> str:
    return SETTING_FA.get(key, key)
