from aiogram.fsm.state import State, StatesGroup


class OnboardingSG(StatesGroup):
    tos = State()
    membership = State()
    timezone = State()


class EventWizardSG(StatesGroup):
    title = State()
    description = State()
    banner = State()
    starts_at = State()
    registration_ends_at = State()
    credentials_send_at = State()
    channel = State()
    region_mode = State()
    capacity = State()
    prizes = State()
    rules = State()
    requirements = State()
    room = State()
    visibility = State()
    preview = State()


class ProfileSG(StatesGroup):
    ff_id = State()
    region = State()


class SupportSG(StatesGroup):
    message = State()


class ReportSG(StatesGroup):
    reason = State()
    body = State()


class AdminSG(StatesGroup):
    user_query = State()
    ban_reason = State()
    channel_ref = State()
    broadcast_title = State()
    broadcast_body = State()
