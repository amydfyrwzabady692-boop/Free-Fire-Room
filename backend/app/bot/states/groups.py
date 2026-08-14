from aiogram.fsm.state import State, StatesGroup


class OnboardingSG(StatesGroup):
    tos = State()
    membership = State()
    timezone = State()


class EventWizardSG(StatesGroup):
    title = State()
    starts_at = State()
    starts_time = State()
    channel = State()
    extra_channels = State()
    capacity = State()
    prizes = State()
    requirements = State()
    room = State()
    preview = State()


class CredsWaitSG(StatesGroup):
    waiting = State()


class AnnounceSG(StatesGroup):
    channel_name = State()
    starts_at = State()
    starts_time = State()
    channel_link = State()
    prize = State()
    extra_links = State()
    preview = State()


class ProfileSG(StatesGroup):
    ff_id = State()
    region = State()


class SupportSG(StatesGroup):
    message = State()


class ReportSG(StatesGroup):
    reason = State()
    body = State()


class ReviewSG(StatesGroup):
    comment = State()


class AdminSG(StatesGroup):
    user_query = State()
    ban_reason = State()
    channel_ref = State()
    broadcast_title = State()
    broadcast_body = State()
