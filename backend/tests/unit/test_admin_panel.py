"""Drive the owner panel handlers directly.

Every section used to answer one tap with 10-20 separate messages; they are now
one message edited in place, so these tests assert both the content and that a
single view is produced.
"""

from __future__ import annotations

import pytest

from app.bot.handlers import admin as panel
from app.core.enums import EventStatus, OrganizerStatus, ReportStatus
from app.models.admin import Admin
from app.models.report import Report
from tests.conftest import make_event, make_organizer, make_user


class Recorder:
    def __init__(self):
        self.views: list = []
        self.alerts: list = []
        self.photos: list = []
        self.sent: list = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    @property
    def last(self) -> str:
        assert self.views, "handler produced no view"
        return self.views[-1][0]

    def buttons(self) -> list:
        markup = self.views[-1][1]
        return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


class FakeMessage:
    def __init__(self, rec):
        self.rec = rec
        self.text = "x"
        self.caption = None
        self.photo = None
        self.bot = rec

    async def edit_text(self, text, reply_markup=None):
        self.rec.views.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup=None):
        pass

    async def answer(self, text, reply_markup=None, **kw):
        self.rec.views.append((text, reply_markup))

    async def answer_photo(self, *a, **kw):
        self.rec.photos.append(a)


class FakeCb:
    def __init__(self, data, rec, user_id=1):
        self.data = data
        self.rec = rec
        self.message = FakeMessage(rec)
        self.bot = rec
        self.from_user = type("U", (), {"id": user_id})()

    async def answer(self, text="", show_alert=False):
        self.rec.alerts.append((text, show_alert))


async def _owner(db):
    user = await db.run_sync(lambda s: make_user(s, 9001))
    db.add(Admin(user_id=user.id, is_active=True, is_super_admin=True))
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_dashboard_renders_and_lists_outstanding_work(async_db):
    db = async_db
    user = await _owner(db)
    host = await db.run_sync(lambda s: make_user(s, 9002))
    org = await db.run_sync(lambda s: make_organizer(s, host))
    org.status = OrganizerStatus.PENDING
    event = await db.run_sync(lambda s: make_event(s, org))
    event.status = EventStatus.PENDING_APPROVAL
    db.add(Report(reporter_id=host.id, reason="no_credentials", body="نیامد", status=ReportStatus.NEW))
    await db.commit()

    rec = Recorder()
    await panel.admin_dash(FakeCb("adm:dash", rec), db, user)

    assert len(rec.views) == 1, "the dashboard must be a single message"
    assert "داشبورد مالک ربات" in rec.last
    assert "نیاز به رسیدگی" in rec.last
    assert "کاستوم منتظر تأیید" in rec.last
    assert "گزارش تخلف باز" in rec.last


@pytest.mark.asyncio
async def test_non_admin_is_refused_everywhere(async_db):
    db = async_db
    await _owner(db)
    stranger = await db.run_sync(lambda s: make_user(s, 9099))
    await db.commit()

    rec = Recorder()
    for handler, data in [
        (panel.admin_dash, "adm:dash"),
        (panel.admin_events, "adm:ev:0"),
        (panel.admin_orgs, "adm:org:0"),
        (panel.admin_reports, "adm:rep:0"),
        (panel.admin_channels, "adm:ch:0"),
        (panel.admin_recent_users, "adm:lu:0"),
        (panel.admin_all_events, "adm:all:0"),
        (panel.admin_winners, "adm:win:0"),
        (panel.admin_anns, "adm:ann:0"),
        (panel.admin_cfg, "adm:cfg"),
    ]:
        await handler(FakeCb(data, rec), db, stranger)
    assert rec.views == [], "a non-admin must never see panel content"
    assert len(rec.alerts) == 10, "every section must refuse explicitly"
    assert all("فقط برای مدیر ربات" in text for text, _ in rec.alerts)


@pytest.mark.asyncio
async def test_pending_customs_page_offers_approve_and_reject(async_db):
    db = async_db
    user = await _owner(db)
    host = await db.run_sync(lambda s: make_user(s, 9003))
    org = await db.run_sync(lambda s: make_organizer(s, host))
    for i in range(7):
        e = await db.run_sync(lambda s, i=i: make_event(s, org, title=f"C{i}", prize_summary=f"{i}00 الماس"))
        e.status = EventStatus.PENDING_APPROVAL
    await db.commit()

    rec = Recorder()
    await panel.admin_events(FakeCb("adm:ev:0", rec), db, user)

    assert len(rec.views) == 1, "seven pending customs must not become seven messages"
    buttons = rec.buttons()
    assert sum(1 for b in buttons if b.startswith("adm:ea:")) == 5, "one page holds five"
    assert "adm:ev:1" in buttons, "there must be a next page"
    assert "adm:home" in buttons


@pytest.mark.asyncio
async def test_empty_sections_explain_themselves(async_db):
    db = async_db
    user = await _owner(db)
    rec = Recorder()

    await panel.admin_events(FakeCb("adm:ev:0", rec), db, user)
    assert "منتظر تأیید نیست" in rec.last

    await panel.admin_reports(FakeCb("adm:rep:0", rec), db, user)
    assert "گزارش بازی وجود ندارد" in rec.last

    await panel.admin_channels(FakeCb("adm:ch:0", rec), db, user)
    assert "هنوز هیچ کانالی ثبت نشده" in rec.last
    assert "adm:ca" in rec.buttons(), "the empty state must still offer the add button"


@pytest.mark.asyncio
async def test_stale_button_for_a_deleted_row_is_handled(async_db):
    db = async_db
    user = await _owner(db)
    rec = Recorder()
    missing = "00000000-0000-0000-0000-000000000000"

    # customs are hard-deleted after the retention window, so old buttons happen
    await panel.admin_event_approve(FakeCb(f"adm:ea:{missing}", rec), db, user)
    await panel.admin_report_ok(FakeCb(f"adm:rok:{missing}", rec), db, user)
    await panel.admin_channel_delete(FakeCb(f"adm:cd:{missing}", rec), db, user)
    assert all(text == "یافت نشد" for text, _ in rec.alerts)
    assert rec.views == []


@pytest.mark.asyncio
async def test_malformed_callback_payload_does_not_crash(async_db):
    db = async_db
    user = await _owner(db)
    rec = Recorder()

    await panel.admin_event_approve(FakeCb("adm:ea:not-a-uuid", rec), db, user)
    await panel.admin_user_by_id(FakeCb("adm:uid:not-a-number", rec), db, user)
    await panel.admin_trust_history(FakeCb("adm:tr:???", rec), db, user)
    assert len(rec.alerts) == 3
    assert rec.views == []


@pytest.mark.asyncio
async def test_user_dossier_shows_trust_and_ban_actions(async_db):
    db = async_db
    user = await _owner(db)
    host = await db.run_sync(lambda s: make_user(s, 9004, username="hoster"))
    org = await db.run_sync(lambda s: make_organizer(s, host))
    org.trust_score = 22.0
    await db.commit()

    rec = Recorder()
    await panel.admin_user_by_id(FakeCb(f"adm:uid:{host.telegram_id}", rec), db, user)

    assert "@hoster" in rec.last
    assert "پرریسک" in rec.last, "a low score must be visible at a glance"
    buttons = rec.buttons()
    assert f"adm:bn:{host.telegram_id}" in buttons
    assert f"adm:bno:{host.telegram_id}" in buttons
    assert f"adm:tr:{org.id}" in buttons
    assert f"adm:ub:{host.telegram_id}" not in buttons, "no unban button when nobody is banned"


@pytest.mark.asyncio
async def test_upholding_a_report_costs_the_organizer_trust(async_db):
    db = async_db
    user = await _owner(db)
    host = await db.run_sync(lambda s: make_user(s, 9005))
    org = await db.run_sync(lambda s: make_organizer(s, host))
    event = await db.run_sync(lambda s: make_event(s, org))
    report = Report(
        reporter_id=host.id,
        event_id=event.id,
        organizer_id=org.id,
        reason="unpaid_prize",
        body="جایزه نداد",
        status=ReportStatus.NEW,
    )
    db.add(report)
    await db.commit()
    before = org.trust_score

    rec = Recorder()
    await panel.admin_report_uphold(FakeCb(f"adm:rup:{report.id}", rec), db, user)
    await db.commit()

    assert org.trust_score < before, "upholding a report used to change nothing"
    assert report.status == ReportStatus.CLOSED
    assert report.resolved_at is not None
    assert "اعتبار برگزارکننده الان" in rec.last


@pytest.mark.asyncio
async def test_event_detail_shows_the_funnel(async_db):
    db = async_db
    user = await _owner(db)
    host = await db.run_sync(lambda s: make_user(s, 9006))
    org = await db.run_sync(lambda s: make_organizer(s, host))
    event = await db.run_sync(lambda s: make_event(s, org, prize_summary="۹۰۰ الماس"))
    await db.commit()

    rec = Recorder()
    await panel.admin_event_detail(FakeCb(f"adm:evd:{event.public_token}", rec), db, user)

    assert "۹۰۰ الماس" in rec.last
    assert "قیف این کاستوم" in rec.last
    assert f"adm:uid:{host.telegram_id}" in rec.buttons()
