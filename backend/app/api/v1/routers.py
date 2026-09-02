from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_organizer, get_super_admin, require_permission
from app.core.enums import BanScope, EventStatus, EventVisibility, RegistrationStatus
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.session import get_db
from app.core.time import utcnow
from app.models.admin import AuditLog, BotContent
from app.models.broadcast import BroadcastCampaign
from app.models.channel import GlobalRequiredChannel
from app.models.event import Event
from app.models.jobs import Delivery
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.report import Report
from app.models.user import Ban, User, UserNote
from app.schemas.common import (
    BanIn,
    BroadcastIn,
    ChannelIn,
    CredentialsIn,
    EventCreateIn,
    LoginOtpIn,
    LoginPasswordIn,
    OtpRequestIn,
    ProfileIn,
    RefreshIn,
    ReasonIn,
    ReportIn,
    RescheduleIn,
    SettingIn,
    TelegramLoginIn,
    TokenResponse,
)
from app.services import auth as auth_svc
from app.services import channels as channel_svc
from app.services import events as event_svc
from app.services import organizers as org_svc
from app.services import settings as settings_svc
from app.services.audit import write_audit
from app.services.reports import format_person, report_label

router_auth = APIRouter(prefix="/auth", tags=["auth"])
router_users = APIRouter(prefix="/users", tags=["users"])
router_events = APIRouter(prefix="/events", tags=["events"])
router_organizers = APIRouter(prefix="/organizers", tags=["organizers"])
router_channels = APIRouter(prefix="/channels", tags=["channels"])
router_regs = APIRouter(prefix="/registrations", tags=["registrations"])
router_refs = APIRouter(prefix="/referrals", tags=["referrals"])
router_notes = APIRouter(prefix="/notifications", tags=["notifications"])
router_reports = APIRouter(prefix="/reports", tags=["reports"])
router_admin = APIRouter(prefix="/admin", tags=["admin"])
router_broadcasts = APIRouter(prefix="/admin/broadcasts", tags=["broadcasts"])
router_analytics = APIRouter(prefix="/admin/analytics", tags=["analytics"])
router_settings = APIRouter(prefix="/admin/settings", tags=["settings"])


def _bot():
    from app.bot.loader import get_bot

    return get_bot()


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else None)


def _tokens(access: str, refresh: str | None) -> TokenResponse:
    from app.core.config import get_settings as _settings

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=_settings().access_token_expire_minutes * 60,
    )


@router_auth.post("/login/password", response_model=TokenResponse)
async def login_password(body: LoginPasswordIn, request: Request, db: AsyncSession = Depends(get_db)):
    access, refresh, _ = await auth_svc.login_super_admin(
        db,
        telegram_id=body.telegram_id,
        password=body.password,
        totp_code=body.totp_code,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return _tokens(access, refresh)


@router_auth.post("/login/otp", response_model=TokenResponse)
async def login_otp(body: LoginOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    access, refresh = await auth_svc.login_with_otp(db, body.telegram_id, body.code, _client_ip(request), request.headers.get("user-agent"))
    await db.commit()
    return _tokens(access, refresh)


@router_auth.post("/login/telegram", response_model=TokenResponse)
async def login_telegram(body: TelegramLoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    access, refresh = await auth_svc.login_telegram_widget(
        db, body.model_dump(), _client_ip(request), request.headers.get("user-agent")
    )
    await db.commit()
    return _tokens(access, refresh)


@router_auth.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Refresh tokens were minted on every login but nothing could spend them,
    so the panel died silently once the 20-minute access token expired."""
    access, refresh = await auth_svc.refresh_tokens(
        db, body.refresh_token, _client_ip(request), request.headers.get("user-agent")
    )
    await db.commit()
    return _tokens(access, refresh)


@router_auth.post("/logout")
async def logout(body: RefreshIn, db: AsyncSession = Depends(get_db)):
    await auth_svc.revoke_refresh(db, body.refresh_token)
    await db.commit()
    return {"ok": True}


@router_auth.post("/otp/request")
async def request_otp(body: OtpRequestIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Unauthenticated by design, so throttle the caller as well as the target:
    otherwise this is a free way to spam any known telegram_id with codes."""
    from app.core.config import get_settings as _settings
    from app.core.rate_limit import hit_rate_limit
    from app.services.users import get_by_telegram

    ip = _client_ip(request) or "unknown"
    await hit_rate_limit(f"rl:otp_req:{ip}", _settings().rate_limit_login_per_minute)

    user = await get_by_telegram(db, body.telegram_id)
    if not user:
        # identical answer either way, so this cannot enumerate accounts
        return {"ok": True}
    code = await auth_svc.issue_otp(body.telegram_id)
    try:
        await _bot().send_message(
            body.telegram_id,
            "کد ورود پنل: `" + code + "`\n" + "این کد ۵ دقیقه اعتبار دارد.",
            parse_mode="Markdown",
        )
    except Exception:  # noqa: BLE001
        return {"ok": True}
    return {"ok": True}


@router_auth.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "id": str(user.id),
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "roles": [ur.role.name for ur in user.roles],
        "timezone": user.timezone,
        "language": user.language,
    }


@router_users.patch("/me")
async def update_me(body: ProfileIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if body.timezone:
        user.timezone = body.timezone
    if body.language:
        user.language = body.language
    if user.profile:
        if body.ff_player_id is not None:
            user.profile.ff_player_id = body.ff_player_id
        if body.region is not None:
            user.profile.region = body.region
        if body.preferred_mode is not None:
            user.profile.preferred_mode = body.preferred_mode
    await db.commit()
    return {"ok": True}


@router_users.post("/me/delete")
async def request_delete(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user.deleted_at = utcnow()
    user.status = "deleted"
    await write_audit(db, action="user_delete_requested", entity_type="user", entity_id=user.id, actor_id=user.id)
    await db.commit()
    return {"ok": True, "message": "درخواست حذف حساب ثبت شد. داده‌های امنیتی برای مدت محدود نگهداری می‌شوند."}


def _event_filters(q: Select, when: str | None, has_capacity: bool | None, game_mode: str | None, region: str | None, verified_only: bool | None, sort: str) -> Select:
    now = utcnow()
    q = q.where(
        Event.deleted_at.is_(None),
        Event.visibility == EventVisibility.PUBLIC,
        Event.status.in_([EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]),
        Event.deep_link_active.is_(True),
    )
    if when == "today":
        q = q.where(Event.starts_at >= now.replace(hour=0, minute=0, second=0, microsecond=0), Event.starts_at < now + timedelta(days=1))
    elif when == "tomorrow":
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        q = q.where(Event.starts_at >= start, Event.starts_at < start + timedelta(days=1))
    elif when == "week":
        q = q.where(Event.starts_at >= now, Event.starts_at < now + timedelta(days=7))
    elif when == "upcoming":
        q = q.where(Event.starts_at >= now)
    if has_capacity:
        q = q.where(Event.confirmed_count < Event.capacity)
    if game_mode:
        q = q.where(Event.game_mode == game_mode)
    if region:
        q = q.where(Event.region == region)
    if sort == "soonest":
        q = q.order_by(Event.starts_at.asc())
    elif sort == "popular":
        q = q.order_by(Event.confirmed_count.desc())
    else:
        q = q.order_by(Event.starts_at.asc())
    return q


@router_events.get("")
async def list_events(
    db: AsyncSession = Depends(get_db),
    when: str | None = None,
    has_capacity: bool | None = None,
    game_mode: str | None = None,
    region: str | None = None,
    verified_only: bool | None = None,
    sort: str = "soonest",
    q: str | None = None,
):
    stmt = select(Event).options(selectinload(Event.organizer), selectinload(Event.channel), selectinload(Event.prizes))
    stmt = _event_filters(stmt, when, has_capacity, game_mode, region, verified_only, sort)
    if q:
        stmt = stmt.where(Event.title.ilike(f"%{q}%"))
    if verified_only:
        stmt = stmt.join(Organizer).where(Organizer.verified_badge.is_(True))
    rows = (await db.scalars(stmt.limit(100))).all()
    out = []
    for e in rows:
        d = event_svc.public_event_dict(e)
        d["organizer_name"] = e.organizer.display_name if e.organizer else None
        d["verified"] = bool(e.organizer and e.organizer.verified_badge)
        d["channel_title"] = e.channel.title if e.channel else None
        d["channel_url"] = f"https://t.me/{e.channel.username}" if e.channel and e.channel.username else None
        out.append(d)
    return {"items": out}


@router_events.get("/{token}")
async def event_detail(token: str, db: AsyncSession = Depends(get_db)):
    e = await db.scalar(
        select(Event)
        .where(Event.public_token == token)
        .options(selectinload(Event.organizer), selectinload(Event.channel), selectinload(Event.prizes), selectinload(Event.required_channels))
    )
    if not e:
        try:
            e = await db.get(Event, UUID(token))
        except Exception:
            e = None
    if not e or e.deleted_at:
        raise NotFoundError("event_not_found", "کاستوم یافت نشد.")
    d = event_svc.public_event_dict(e)
    d["organizer_name"] = e.organizer.display_name if e.organizer else None
    d["verified"] = bool(e.organizer and e.organizer.verified_badge)
    d["channel_title"] = e.channel.title if e.channel else None
    d["rules_text"] = e.rules_text
    d["prizes"] = [{"place": p.place, "title": p.title, "description": p.description} for p in e.prizes]
    return d


@router_events.post("")
async def create_event(body: EventCreateIn, org=Depends(get_organizer), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_svc.create_event(db, org, body.model_dump(), user.id)
    await db.commit()
    return event_svc.public_event_dict(event)


@router_events.post("/{event_id}/submit")
async def submit_event(event_id: UUID, org=Depends(get_organizer), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise ForbiddenError()
    await event_svc.submit_for_publish(db, event, user.id)
    await db.commit()
    return {"status": event.status}


@router_events.post("/{event_id}/copy")
async def copy_my_event(event_id: UUID, org=Depends(get_organizer), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await db.scalar(
        select(Event).where(Event.id == event_id).options(selectinload(Event.prizes), selectinload(Event.required_channels))
    )
    if not event or event.organizer_id != org.id:
        raise ForbiddenError()
    copy = await event_svc.copy_event(db, event, org, user.id)
    await db.commit()
    return event_svc.public_event_dict(copy)


@router_events.post("/{event_id}/results")
async def publish_results(event_id: UUID, body: ReasonIn, org=Depends(get_organizer), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.services.notifications import notify
    from app.core.enums import NotificationKind

    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise ForbiddenError()
    regs = (await db.scalars(select(Registration).where(Registration.event_id == event.id, Registration.status == "confirmed"))).all()
    for r in regs:
        await notify(db, r.user_id, NotificationKind.RESULT, "نتیجه کاستوم", body.reason, event_id=event.id)
    await write_audit(db, action="results_published", entity_type="event", entity_id=event.id, actor_id=user.id)
    await db.commit()
    return {"ok": True, "notified": len(regs)}


@router_events.post("/{event_id}/cancel")
async def cancel_my_event(event_id: UUID, body: ReasonIn, org=Depends(get_organizer), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise ForbiddenError()
    await event_svc.cancel_event(db, event, user.id, body.reason)
    await db.commit()
    return {"status": event.status}


@router_events.put("/{event_id}/credentials")
async def set_creds(event_id: UUID, body: CredentialsIn, org=Depends(get_organizer), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise ForbiddenError()
    await event_svc.update_credentials(db, event, user.id, body.room_id, body.room_password)
    await db.commit()
    return {"ok": True, "message": "اطلاعات اتاق ذخیره شد و تا زمان ارسال نمایش داده نمی‌شود."}


@router_organizers.get("/me")
async def my_org(org=Depends(get_organizer)):
    return {
        "id": str(org.id),
        "status": org.status,
        "verified_badge": org.verified_badge,
        "trust_score": org.trust_score,
        "display_name": org.display_name,
    }


@router_organizers.get("/me/events")
async def my_events(org=Depends(get_organizer), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Event).where(Event.organizer_id == org.id, Event.deleted_at.is_(None)).order_by(Event.starts_at.desc()))).all()
    return {"items": [event_svc.public_event_dict(e) for e in rows]}


@router_organizers.get("/me/events/{event_id}/participants")
async def my_participants(event_id: UUID, org=Depends(get_organizer), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise ForbiddenError("forbidden", "فقط شرکت‌کنندگان کاستوم خودتان قابل مشاهده است.")
    regs = (
        await db.scalars(
            select(Registration)
            .where(Registration.event_id == event.id)
            .options(selectinload(Registration.user).selectinload(User.profile))
        )
    ).all()
    items = []
    for r in regs:
        items.append(
            {
                "registration_id": str(r.id),
                "status": r.status,
                "telegram_id": r.user.telegram_id,
                "username": r.user.username,
                "name": r.user.first_name,
                "ff_player_id": r.user.profile.ff_player_id if r.user.profile else None,
                "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
            }
        )
    return {"items": items}


@router_organizers.get("/me/events/{event_id}/deliveries")
async def my_deliveries(event_id: UUID, org=Depends(get_organizer), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise ForbiddenError()
    rows = (await db.scalars(select(Delivery).where(Delivery.event_id == event.id, Delivery.kind == "room_credentials"))).all()
    return {
        "items": [
            {
                "id": str(d.id),
                "status": d.status,
                "attempts": d.attempts,
                "sent_at": d.sent_at.isoformat() if d.sent_at else None,
                "error": d.error_message,
            }
            for d in rows
        ]
    }


@router_channels.post("/connect")
async def connect_channel(body: ChannelIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    bot = _bot()
    ch = await channel_svc.connect_organizer_channel(db, bot, user, body.chat_ref)
    await db.commit()
    return {"id": str(ch.id), "title": ch.title, "telegram_chat_id": ch.telegram_chat_id, "bot_is_admin": ch.bot_is_admin}


@router_regs.post("/{token}")
async def register_via_api(token: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.services.registration import register_user

    event = await db.scalar(select(Event).where(Event.public_token == token))
    if not event:
        raise NotFoundError("event_not_found", "کاستوم یافت نشد.")
    bot = _bot()
    result = await register_user(db, user=user, event=event, bot=bot, source="api")
    await db.commit()
    return {
        "status": result.registration.status,
        "waitlisted": result.waitlisted,
        "checklist": [
            {"type": i.requirement_type, "label": i.label, "status": i.status, "detail": i.detail}
            for i in (result.checklist or [])
        ],
    }


@router_organizers.get("/me/events/{event_id}/export")
async def export_participants(event_id: UUID, org=Depends(get_organizer), db: AsyncSession = Depends(get_db)):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise ForbiddenError()
    regs = (
        await db.scalars(
            select(Registration)
            .where(Registration.event_id == event.id)
            .options(selectinload(Registration.user).selectinload(User.profile))
        )
    ).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["telegram_id", "username", "status", "ff_player_id"])
    for r in regs:
        w.writerow([r.user.telegram_id, r.user.username or "", r.status, (r.user.profile.ff_player_id if r.user.profile else "")])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=participants.csv"},
    )


@router_notes.get("")
async def my_notifications(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.models.jobs import Notification

    rows = (
        await db.scalars(
            select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(50)
        )
    ).all()
    return {
        "items": [
            {"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body, "is_read": n.is_read}
            for n in rows
        ]
    }


@router_refs.get("/me")
async def my_referral(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db), event_id: UUID | None = None):
    from app.services.referrals import get_or_create_link
    from app.core.config import get_settings

    link = await get_or_create_link(db, user.id, event_id)
    await db.commit()
    bot = get_settings().bot_username
    return {"token": link.token, "valid_count": link.valid_count, "url": f"https://t.me/{bot}?start=ref_{link.token}"}


@router_reports.post("")
async def create_report(
    body: ReportIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """This endpoint had lost its decorator and signature: the body was left
    dangling after my_referral returned, so router_reports had no routes at all
    and nothing could file a report through the API."""
    from app.core.enums import ReportReason

    valid = {r.value for r in ReportReason}
    if body.reason not in valid:
        raise ValidationAppError("bad_reason", "دلیل گزارش نامعتبر است.", {"allowed": sorted(valid)})
    if not body.event_id and not body.organizer_id:
        raise ValidationAppError("target_required", "کاستوم یا برگزارکندهٔ مورد گزارش را مشخص کنید.")

    organizer_id = body.organizer_id
    if body.event_id:
        event = await db.get(Event, body.event_id)
        if not event:
            raise NotFoundError("event_not_found", "کاستوم یافت نشد.")
        organizer_id = organizer_id or event.organizer_id

    # one open report per person per target, so the queue cannot be flooded
    existing = await db.scalar(
        select(Report).where(
            Report.reporter_id == user.id,
            Report.event_id == body.event_id,
            Report.status == "new",
        )
    )
    if existing:
        raise ConflictError("already_reported", "قبلاً برای این کاستوم گزارش ثبت کرده‌اید.")

    row = Report(
        reporter_id=user.id,
        event_id=body.event_id,
        organizer_id=organizer_id,
        reason=body.reason,
        body=body.body,
        status="new",
    )
    db.add(row)
    await write_audit(
        db, action="report_created", entity_type="report", entity_id=row.id, actor_id=user.id
    )
    await db.commit()
    return {"id": str(row.id), "status": row.status}


@router_reports.get("/me")
async def my_reports(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.scalars(
            select(Report).where(Report.reporter_id == user.id).order_by(Report.created_at.desc()).limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "reason": r.reason,
                "reason_label": report_label(r.reason),
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router_admin.get("/dashboard")
async def dashboard(_: User = Depends(require_permission("admin.dashboard")), db: AsyncSession = Depends(get_db)):
    now = utcnow()
    day = now - timedelta(days=1)
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)
    users = await db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None)))
    new_users = await db.scalar(select(func.count()).select_from(User).where(User.created_at >= day))
    dau = await db.scalar(select(func.count()).select_from(User).where(User.last_seen_at >= day))
    wau = await db.scalar(select(func.count()).select_from(User).where(User.last_seen_at >= week))
    mau = await db.scalar(select(func.count()).select_from(User).where(User.last_seen_at >= month))
    banned = await db.scalar(select(func.count()).select_from(Ban).where(Ban.is_active.is_(True)))
    orgs = await db.scalar(select(func.count()).select_from(Organizer))
    events_active = await db.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.deleted_at.is_(None),
            Event.status.in_([EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]),
        )
    )
    pending_events = await db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.deleted_at.is_(None), Event.status == EventStatus.PENDING_APPROVAL)
    )
    pending_orgs = await db.scalar(
        select(func.count()).select_from(Organizer).where(Organizer.status == "pending")
    )
    open_reports = await db.scalar(
        select(func.count()).select_from(Report).where(Report.status == "new")
    )
    regs = await db.scalar(select(func.count()).select_from(Registration).where(Registration.status == RegistrationStatus.CONFIRMED))
    sent = await db.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "sent"))
    failed = await db.scalar(select(func.count()).select_from(Delivery).where(Delivery.status.in_(["failed", "permanent_fail"])))
    return {
        "users": users,
        "new_users_24h": new_users,
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "banned": banned,
        "organizers": orgs,
        "pending_organizers": pending_orgs,
        "active_events": events_active,
        "pending_events": pending_events,
        "open_reports": open_reports,
        "confirmed_registrations": regs,
        "deliveries_sent": sent,
        "deliveries_failed": failed,
    }


@router_admin.get("/users")
async def admin_users(
    q: str | None = None,
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("admin.users")),
):
    stmt = select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc())
    if q:
        if q.isdigit():
            stmt = stmt.where(or_(User.telegram_id == int(q), User.username.ilike(f"%{q}%"), User.first_name.ilike(f"%{q}%")))
        else:
            stmt = stmt.where(or_(User.username.ilike(f"%{q}%"), User.first_name.ilike(f"%{q}%")))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.scalars(stmt.offset(page * size).limit(size))).all()
    return {
        "total": int(total or 0),
        "page": page,
        "size": size,
        "items": [
            {
                "id": str(u.id),
                "telegram_id": u.telegram_id,
                "is_banned": u.status == "banned",
                "username": u.username,
                "first_name": u.first_name,
                "status": u.status,
                "created_at": u.created_at.isoformat(),
            }
            for u in rows
        ]
    }


@router_admin.post("/users/{user_id}/ban")
async def ban_user(user_id: UUID, body: BanIn, admin_user: User = Depends(require_permission("admin.users")), db: AsyncSession = Depends(get_db)):
    target = await db.get(User, user_id)
    if not target:
        raise NotFoundError()
    ban = Ban(user_id=target.id, scope=body.scope, reason=body.reason, expires_at=body.expires_at, is_active=True, created_by=admin_user.id)
    db.add(ban)
    if body.scope == BanScope.BOT:
        target.status = "banned"
    await write_audit(db, action="user_banned", entity_type="user", entity_id=target.id, actor_id=admin_user.id, extra={"scope": body.scope, "reason": body.reason})
    await db.commit()
    return {"ok": True}


@router_admin.post("/users/{user_id}/unban")
async def unban_user(user_id: UUID, admin_user: User = Depends(require_permission("admin.users")), db: AsyncSession = Depends(get_db)):
    bans = (await db.scalars(select(Ban).where(Ban.user_id == user_id, Ban.is_active.is_(True)))).all()
    for b in bans:
        b.is_active = False
    user = await db.get(User, user_id)
    if user:
        user.status = "active"
    await write_audit(db, action="user_unbanned", entity_type="user", entity_id=user_id, actor_id=admin_user.id)
    await db.commit()
    return {"ok": True}


@router_admin.post("/users/{user_id}/notes")
async def add_note(user_id: UUID, body: ReasonIn, admin_user: User = Depends(require_permission("admin.users")), db: AsyncSession = Depends(get_db)):
    db.add(UserNote(user_id=user_id, author_id=admin_user.id, body=body.reason, is_internal=True))
    await db.commit()
    return {"ok": True}


@router_admin.get("/events")
async def admin_events(status: str | None = None, db: AsyncSession = Depends(get_db), _: User = Depends(require_permission("admin.events"))):
    stmt = select(Event).where(Event.deleted_at.is_(None)).order_by(Event.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Event.status == status)
    rows = (await db.scalars(stmt.options(selectinload(Event.organizer)))).all()
    items = []
    for e in rows:
        d = event_svc.public_event_dict(e)
        d["organizer_name"] = e.organizer.display_name if e.organizer else None
        items.append(d)
    return {"items": items}


@router_admin.post("/events/{event_id}/approve")
async def admin_approve(event_id: UUID, admin_user: User = Depends(require_permission("admin.events")), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise NotFoundError()
    await event_svc.approve_event(db, event, admin_user.id)
    await db.commit()
    return {"status": event.status}


@router_admin.post("/events/{event_id}/reject")
async def admin_reject(event_id: UUID, body: ReasonIn, admin_user: User = Depends(require_permission("admin.events")), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise NotFoundError()
    await event_svc.reject_event(db, event, admin_user.id, body.reason)
    await db.commit()
    return {"status": event.status}


@router_admin.post("/events/{event_id}/cancel")
async def admin_cancel(event_id: UUID, body: ReasonIn, admin_user: User = Depends(require_permission("admin.events")), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise NotFoundError()
    await event_svc.cancel_event(db, event, admin_user.id, body.reason)
    await db.commit()
    return {"status": event.status}


@router_admin.post("/events/{event_id}/reschedule")
async def admin_reschedule(event_id: UUID, body: RescheduleIn, admin_user: User = Depends(require_permission("admin.events")), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise NotFoundError()
    await event_svc.reschedule_event(db, event, admin_user.id, body.starts_at, body.registration_ends_at, body.credentials_send_at)
    await db.commit()
    return {"status": event.status}


@router_admin.post("/events/{event_id}/feature")
async def feature_event(event_id: UUID, admin_user: User = Depends(require_permission("admin.events")), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise NotFoundError()
    event.featured = not event.featured
    await write_audit(db, action="event_featured", entity_type="event", entity_id=event.id, actor_id=admin_user.id)
    await db.commit()
    return {"featured": event.featured}


@router_organizers.get("/me/events/{event_id}/funnel")
async def my_event_funnel(
    event_id: UUID,
    org=Depends(get_organizer),
    db: AsyncSession = Depends(get_db),
):
    from app.services.funnel import event_funnel

    event = await db.get(Event, event_id)
    if not event or event.organizer_id != org.id:
        raise NotFoundError()
    return await event_funnel(db, event.id)


@router_admin.get("/organizers")
async def admin_orgs(db: AsyncSession = Depends(get_db), _: User = Depends(require_permission("admin.organizers"))):
    rows = (await db.scalars(select(Organizer).options(selectinload(Organizer.user)).order_by(Organizer.created_at.desc()))).all()
    return {
        "items": [
            {
                "id": str(o.id),
                "status": o.status,
                "display_name": o.display_name,
                "verified_badge": o.verified_badge,
                "trust_score": o.trust_score,
                "telegram_id": o.user.telegram_id if o.user else None,
            }
            for o in rows
        ]
    }


@router_admin.post("/organizers/{org_id}/approve")
async def admin_org_approve(org_id: UUID, admin_user: User = Depends(require_permission("admin.organizers")), db: AsyncSession = Depends(get_db)):
    org = await db.get(Organizer, org_id)
    if not org:
        raise NotFoundError()
    await org_svc.approve_organizer(db, org, admin_user.id, verified=True)
    await db.commit()
    return {"status": org.status}


@router_admin.post("/organizers/{org_id}/reject")
async def admin_org_reject(org_id: UUID, body: ReasonIn, admin_user: User = Depends(require_permission("admin.organizers")), db: AsyncSession = Depends(get_db)):
    org = await db.get(Organizer, org_id)
    if not org:
        raise NotFoundError()
    await org_svc.reject_organizer(db, org, admin_user.id, body.reason)
    await db.commit()
    return {"status": org.status}


@router_admin.get("/global-channels")
async def list_global_channels(db: AsyncSession = Depends(get_db), _: User = Depends(require_permission("admin.channels"))):
    rows = (await db.scalars(select(GlobalRequiredChannel).options(selectinload(GlobalRequiredChannel.channel)))).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "title": r.channel.title if r.channel else None,
                "username": r.channel.username if r.channel else None,
                "bot_is_admin": r.channel.bot_is_admin if r.channel else False,
                "scope": r.scope,
                "is_active": r.is_active,
                "sort_order": r.sort_order,
            }
            for r in rows
        ]
    }


@router_admin.post("/global-channels")
async def add_global_channel(body: ChannelIn, admin_user: User = Depends(require_permission("admin.channels")), db: AsyncSession = Depends(get_db)):
    bot = _bot()
    row = await channel_svc.add_global_required_channel(db, bot, admin_user.id, body.chat_ref, scope=body.scope, sort_order=body.sort_order)
    await db.commit()
    return {"id": str(row.id)}


@router_admin.post("/global-channels/{row_id}/toggle")
async def toggle_global_channel(row_id: UUID, admin_user: User = Depends(require_permission("admin.channels")), db: AsyncSession = Depends(get_db)):
    row = await db.get(GlobalRequiredChannel, row_id)
    if not row:
        raise NotFoundError()
    row.is_active = not row.is_active
    await write_audit(db, action="global_channel_toggled", entity_type="global_required_channel", entity_id=row.id, actor_id=admin_user.id)
    await db.commit()
    return {"is_active": row.is_active}


@router_admin.delete("/global-channels/{row_id}")
async def remove_global_channel(
    row_id: UUID,
    admin_user: User = Depends(require_permission("admin.channels")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(GlobalRequiredChannel, row_id)
    if not row:
        raise NotFoundError()
    await db.delete(row)
    await write_audit(
        db,
        action="global_channel_removed",
        entity_type="global_required_channel",
        entity_id=row_id,
        actor_id=admin_user.id,
    )
    await db.commit()
    return {"ok": True}


@router_admin.get("/organizers/{org_id}/trust")
async def organizer_trust(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("admin.organizers")),
):
    from app.services import trust as trust_svc

    org = await db.get(Organizer, org_id)
    if not org:
        raise NotFoundError()
    rows = await trust_svc.history(db, org.id, limit=25)
    return {
        "score": float(org.trust_score or 0),
        "badge": trust_svc.badge(org.trust_score),
        "events": [
            {
                "at": r.created_at.isoformat(),
                "type": r.event_type,
                "delta": r.delta,
                "reason": r.reason,
            }
            for r in rows
        ],
    }


@router_admin.get("/events/{event_id}/funnel")
async def admin_event_funnel(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("admin.events")),
):
    from app.services.funnel import event_funnel

    event = await db.get(Event, event_id)
    if not event:
        raise NotFoundError()
    return await event_funnel(db, event.id)


@router_admin.get("/reports")
async def admin_reports(db: AsyncSession = Depends(get_db), _: User = Depends(require_permission("admin.reports"))):
    rows = (await db.scalars(select(Report).order_by(Report.created_at.desc()).limit(200))).all()
    items = []
    for r in rows:
        event = await db.get(Event, r.event_id) if r.event_id else None
        reporter = await db.get(User, r.reporter_id)
        org = await db.get(Organizer, r.organizer_id) if r.organizer_id else None
        org_user = await db.get(User, org.user_id) if org else None
        items.append(
            {
                "id": str(r.id),
                "reason": r.reason,
                "reason_label": report_label(r.reason),
                "status": r.status,
                "body": r.body,
                "created_at": r.created_at.isoformat(),
                "event_title": event.title if event else None,
                "reporter": format_person(reporter),
                "organizer": format_person(org_user),
            }
        )
    return {"items": items}


@router_admin.post("/reports/{report_id}/status")
async def report_status(
    report_id: UUID,
    status: str = Query(..., pattern="^(new|in_review|confirmed|rejected|closed)$"),
    note: str | None = Query(None, max_length=500),
    admin_user: User = Depends(require_permission("admin.reports")),
    db: AsyncSession = Depends(get_db),
):
    """`status` used to be an unvalidated free-text query param, so any string
    could be written straight into the column."""
    from app.services import trust as trust_svc

    row = await db.get(Report, report_id)
    if not row:
        raise NotFoundError()
    row.status = status
    if note:
        row.admin_note = note
    if status in {"confirmed", "rejected", "closed"}:
        row.resolved_at = utcnow()
    # upholding a report is the moment the organizer's trust should move
    if status == "confirmed" and row.organizer_id:
        org = await db.get(Organizer, row.organizer_id)
        if org:
            rule = "prize_unpaid_reported" if row.reason == "unpaid_prize" else "report_upheld"
            await trust_svc.record(
                db, org, rule, related_event_id=row.event_id, actor_id=admin_user.id
            )
    await write_audit(
        db,
        action="report_updated",
        entity_type="report",
        entity_id=row.id,
        actor_id=admin_user.id,
        after={"status": status},
    )
    await db.commit()
    return {"status": row.status}


@router_admin.get("/audit")
async def audit_logs(action: str | None = None, db: AsyncSession = Depends(get_db), _: User = Depends(require_permission("admin.audit"))):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = (await db.scalars(stmt)).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "created_at": r.created_at.isoformat(),
                "extra": r.extra,
            }
            for r in rows
        ]
    }


@router_admin.get("/content")
async def list_content(db: AsyncSession = Depends(get_db), _: User = Depends(require_permission("admin.content"))):
    rows = (await db.scalars(select(BotContent))).all()
    return {"items": [{"key": r.key, "locale": r.locale, "body": r.body} for r in rows]}


@router_admin.put("/content/{key}")
async def put_content(key: str, body: ReasonIn, admin_user: User = Depends(require_permission("admin.content")), db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(BotContent).where(BotContent.key == key))
    if row is None:
        row = BotContent(key=key, locale="fa", body=body.reason, updated_by=admin_user.id)
        db.add(row)
    else:
        row.body = body.reason
        row.updated_by = admin_user.id
    await db.commit()
    return {"ok": True}


@router_broadcasts.post("")
async def create_broadcast(body: BroadcastIn, admin_user: User = Depends(require_permission("admin.broadcasts")), db: AsyncSession = Depends(get_db)):
    row = BroadcastCampaign(
        title=body.title,
        body=body.body,
        media_type=body.media_type,
        media_file_id=body.media_file_id,
        buttons=body.buttons,
        targeting=body.targeting,
        status="draft",
        scheduled_at=body.scheduled_at,
        created_by=admin_user.id,
    )
    db.add(row)
    await write_audit(db, action="broadcast_created", entity_type="broadcast", entity_id=row.id, actor_id=admin_user.id)
    await db.commit()
    return {"id": str(row.id), "status": row.status}


@router_broadcasts.post("/{campaign_id}/confirm")
async def confirm_broadcast(campaign_id: UUID, admin_user: User = Depends(require_permission("admin.broadcasts")), db: AsyncSession = Depends(get_db)):
    row = await db.get(BroadcastCampaign, campaign_id)
    if not row:
        raise NotFoundError()
    if row.status != "draft":
        # confirming twice would send the whole campaign twice
        raise ConflictError("broadcast_already_confirmed", "این ارسال همگانی قبلاً تأیید شده است.")
    row.status = "scheduled" if row.scheduled_at else "running"
    row.confirmed_by = admin_user.id
    row.confirmed_at = utcnow()
    await write_audit(db, action="broadcast_confirmed", entity_type="broadcast", entity_id=row.id, actor_id=admin_user.id)
    await db.commit()
    from app.workers.enqueue import spawn
    from app.workers.tasks import run_broadcast

    spawn(run_broadcast, str(row.id))
    return {"status": row.status}


@router_broadcasts.post("/{campaign_id}/pause")
async def pause_broadcast(campaign_id: UUID, admin_user: User = Depends(require_permission("admin.broadcasts")), db: AsyncSession = Depends(get_db)):
    row = await db.get(BroadcastCampaign, campaign_id)
    if not row:
        raise NotFoundError()
    row.status = "paused"
    await db.commit()
    return {"status": row.status}


@router_settings.get("")
async def get_settings_admin(db: AsyncSession = Depends(get_db), _: User = Depends(get_super_admin)):
    return await settings_svc.all_settings(db)


@router_settings.put("")
async def put_setting(body: SettingIn, admin: User = Depends(get_super_admin), db: AsyncSession = Depends(get_db)):
    await settings_svc.set_setting(db, body.key, body.value, updated_by=admin.id, description=body.description)
    await write_audit(db, action="setting_changed", entity_type="system_setting", entity_id=body.key, actor_id=admin.id, after={"value": body.value})
    await db.commit()
    return {"ok": True}


@router_analytics.get("/overview")
async def analytics_overview(db: AsyncSession = Depends(get_db), _: User = Depends(require_permission("admin.dashboard"))):
    popular = (
        await db.scalars(select(Event).where(Event.deleted_at.is_(None)).order_by(Event.confirmed_count.desc()).limit(10))
    ).all()
    return {"popular_events": [{"title": e.title, "confirmed": e.confirmed_count, "id": str(e.id)} for e in popular]}


def all_routers():
    return [
        router_auth,
        router_users,
        router_events,
        router_organizers,
        router_channels,
        router_regs,
        router_refs,
        router_notes,
        router_reports,
        router_admin,
        router_broadcasts,
        router_analytics,
        router_settings,
    ]
