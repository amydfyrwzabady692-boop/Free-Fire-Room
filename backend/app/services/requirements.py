from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.enums import BanScope, RequirementStatus, RequirementType
from app.models.channel import Channel, GlobalRequiredChannel
from app.models.event import Event, EventRequiredChannel, EventRequirement
from app.models.referral import Referral
from app.models.registration import Registration
from app.models.user import User
from app.services.bans import is_banned, is_banned_sync

if TYPE_CHECKING:
    from aiogram import Bot


@dataclass
class CheckItem:
    requirement_type: str
    label: str
    status: str
    detail: str | None = None
    action: str | None = None
    ref_id: str | None = None
    url: str | None = None


@dataclass
class Checklist:
    items: list[CheckItem] = field(default_factory=list)
    all_ok: bool = True

    def add(self, item: CheckItem) -> None:
        self.items.append(item)
        if item.status != RequirementStatus.DONE:
            self.all_ok = False


def _channel_url(channel: Channel) -> str | None:
    if channel.username:
        return f"https://t.me/{channel.username.lstrip('@')}"
    return channel.invite_link


async def build_event_requirements(event: Event) -> list[EventRequirement]:
    """Ensure built-in requirements exist in memory (DB rows created at publish)."""
    return list(event.requirements)


async def evaluate_requirements(
    db: AsyncSession,
    *,
    user: User,
    event: Event,
    bot: Bot | None,
    registration: Registration | None = None,
) -> Checklist:
    checklist = Checklist()
    now = datetime.now(UTC)

    ban = await is_banned(db, user, BanScope.PARTICIPATE)
    checklist.add(
        CheckItem(
            RequirementType.NOT_BANNED,
            "عدم محدودیت حساب",
            RequirementStatus.DONE if not ban else RequirementStatus.REJECTED,
            None if not ban else "حساب شما برای شرکت محدود شده است.",
        )
    )

    if event.confirmed_count >= event.capacity and (
        registration is None or registration.status != "confirmed"
    ):
        cap_status = RequirementStatus.NOT_DONE
        cap_detail = "ظرفیت تکمیل شده است."
        if event.waitlist_enabled:
            cap_detail = "ظرفیت تکمیل است؛ در صورت واجد شرایط بودن به لیست انتظار می‌روید."
            cap_status = RequirementStatus.PENDING_REVIEW
        checklist.add(CheckItem(RequirementType.CAPACITY, "ظرفیت خالی", cap_status, cap_detail))
    else:
        checklist.add(CheckItem(RequirementType.CAPACITY, "ظرفیت خالی", RequirementStatus.DONE))

    if event.require_rules_accept:
        accepted = bool(registration and registration.rules_accepted_at)
        checklist.add(
            CheckItem(
                RequirementType.RULES_ACCEPT,
                "پذیرش قوانین کاستوم",
                RequirementStatus.DONE if accepted else RequirementStatus.NOT_DONE,
                None if accepted else "باید قوانین این کاستوم را بپذیرید.",
                action="accept_rules",
            )
        )

    if event.require_profile_complete:
        profile = user.profile
        ok = bool(profile and profile.region)
        checklist.add(
            CheckItem(
                RequirementType.PROFILE_COMPLETE,
                "تکمیل پروفایل",
                RequirementStatus.DONE if ok else RequirementStatus.NOT_DONE,
                None if ok else "منطقه بازی را در پروفایل ثبت کنید.",
                action="edit_profile",
            )
        )

    if event.require_ff_player_id:
        profile = user.profile
        ok = bool(profile and profile.ff_player_id)
        checklist.add(
            CheckItem(
                RequirementType.FF_PLAYER_ID,
                "ثبت شناسه Free Fire",
                RequirementStatus.DONE if ok else RequirementStatus.NOT_DONE,
                None if ok else "شناسه بازیکن Free Fire را در پروفایل وارد کنید.",
                action="edit_profile",
            )
        )

    if event.required_referrals > 0:
        valid = await db.scalar(
            select(func.count())
            .select_from(Referral)
            .where(
                Referral.inviter_id == user.id,
                Referral.event_id == event.id,
                Referral.is_valid.is_(True),
            )
        )
        ok = int(valid or 0) >= event.required_referrals
        checklist.add(
            CheckItem(
                RequirementType.REFERRALS,
                f"دعوت {event.required_referrals} کاربر معتبر",
                RequirementStatus.DONE if ok else RequirementStatus.NOT_DONE,
                f"{int(valid or 0)} از {event.required_referrals} دعوت معتبر",
                action="invite",
            )
        )

    global_channels = (
        await db.scalars(
            select(GlobalRequiredChannel)
            .where(
                GlobalRequiredChannel.is_active.is_(True),
                GlobalRequiredChannel.applies_to_events.is_(True),
            )
        )
    ).all()
    for grc in global_channels:
        if grc.starts_at and grc.starts_at > now:
            continue
        if grc.ends_at and grc.ends_at < now:
            continue
        channel = await db.get(Channel, grc.channel_id)
        if not channel:
            continue
        if not channel.bot_is_admin:
            checklist.add(
                CheckItem(
                    RequirementType.GLOBAL_CHANNEL_MEMBERSHIP,
                    f"عضویت در {channel.title}",
                    RequirementStatus.NOT_DONE,
                    "ربات الان ادمین این کانال نیست؛ عضویت قابل بررسی نیست.",
                    url=_channel_url(channel),
                    ref_id=str(channel.id),
                )
            )
            continue
        item = await _membership_item(bot, user, channel, global_flag=True)
        checklist.add(item)

    req_channels = (
        await db.scalars(
            select(EventRequiredChannel).where(
                EventRequiredChannel.event_id == event.id,
                EventRequiredChannel.is_active.is_(True),
            )
        )
    ).all()
    for erc in req_channels:
        channel = await db.get(Channel, erc.channel_id)
        if not channel:
            continue
        if not channel.bot_is_admin:
            checklist.add(
                CheckItem(
                    RequirementType.CHANNEL_MEMBERSHIP,
                    f"عضویت در {channel.title}",
                    RequirementStatus.NOT_DONE,
                    "ربات الان ادمین این کانال نیست؛ عضویت قابل بررسی نیست. برگزارکننده باید دوباره ربات را ادمین کند.",
                    ref_id=str(channel.id),
                )
            )
            continue
        checklist.add(await _membership_item(bot, user, channel, global_flag=False))

    return checklist


async def _membership_item(bot, user: User, channel: Channel, global_flag: bool) -> CheckItem:
    rtype = RequirementType.GLOBAL_CHANNEL_MEMBERSHIP if global_flag else RequirementType.CHANNEL_MEMBERSHIP
    label = f"عضویت در {channel.title}"
    url = _channel_url(channel)
    if bot is None:
        return CheckItem(rtype, label, RequirementStatus.PENDING_REVIEW, "در حال بررسی عضویت", url=url)
    from app.services.telegram_ops import get_membership

    result = await get_membership(bot, channel.telegram_chat_id, user.telegram_id)
    if result.error == "bot_not_admin":
        return CheckItem(
            rtype,
            label,
            RequirementStatus.NOT_DONE,
            "ربات دسترسی لازم برای بررسی عضویت را ندارد. برگزارکننده باید ربات را ادمین کند.",
            url=url,
            ref_id=str(channel.id),
        )
    ok = result.ok
    return CheckItem(
        rtype,
        label,
        RequirementStatus.DONE if ok else RequirementStatus.NOT_DONE,
        None if ok else "هنوز عضو این کانال نیستید.",
        action="join_channel",
        url=url,
        ref_id=str(channel.id),
    )


def evaluate_capacity_only(event: Event, already_confirmed: bool) -> bool:
    if already_confirmed:
        return True
    return event.confirmed_count < event.capacity


# --- Sync variants used by Celery workers ---

def evaluate_requirements_sync(db: Session, user: User, event: Event, memberships: dict[int, bool]) -> Checklist:
    checklist = Checklist()
    ban = is_banned_sync(db, user, BanScope.PARTICIPATE)
    checklist.add(
        CheckItem(
            RequirementType.NOT_BANNED,
            "عدم محدودیت حساب",
            RequirementStatus.DONE if not ban else RequirementStatus.REJECTED,
        )
    )
    if event.require_rules_accept:
        # worker recheck uses last known registration flags
        pass
    for chat_id, ok in memberships.items():
        checklist.add(
            CheckItem(
                RequirementType.CHANNEL_MEMBERSHIP,
                f"channel:{chat_id}",
                RequirementStatus.DONE if ok else RequirementStatus.NOT_DONE,
            )
        )
    if event.required_referrals > 0:
        valid = db.scalar(
            select(func.count())
            .select_from(Referral)
            .where(
                Referral.inviter_id == user.id,
                Referral.event_id == event.id,
                Referral.is_valid.is_(True),
            )
        )
        ok = int(valid or 0) >= event.required_referrals
        checklist.add(
            CheckItem(
                RequirementType.REFERRALS,
                "referrals",
                RequirementStatus.DONE if ok else RequirementStatus.NOT_DONE,
            )
        )
    return checklist
