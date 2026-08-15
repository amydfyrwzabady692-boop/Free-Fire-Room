from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    BANNED = "banned"
    DELETED = "deleted"


class BanScope(StrEnum):
    BOT = "bot"
    ORGANIZE = "organize"
    PARTICIPATE = "participate"


class RoleName(StrEnum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MODERATOR = "moderator"
    ORGANIZER = "organizer"
    PLAYER = "player"


class OrganizerStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class EventStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    PUBLISHED = "published"
    FULL = "full"
    STARTED = "started"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class EventVisibility(StrEnum):
    PUBLIC = "public"
    UNLISTED = "unlisted"


class GameMode(StrEnum):
    SOLO = "solo"
    DUO = "duo"
    SQUAD = "squad"


class RequirementType(StrEnum):
    CHANNEL_MEMBERSHIP = "channel_membership"
    GLOBAL_CHANNEL_MEMBERSHIP = "global_channel_membership"
    REFERRALS = "referrals"
    RULES_ACCEPT = "rules_accept"
    PROFILE_COMPLETE = "profile_complete"
    FF_PLAYER_ID = "ff_player_id"
    NOT_BANNED = "not_banned"
    CAPACITY = "capacity"


class RequirementStatus(StrEnum):
    DONE = "done"
    NOT_DONE = "not_done"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RegistrationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    CANCELLED = "cancelled"
    INELIGIBLE = "ineligible"


class JobType(StrEnum):
    SEND_CREDENTIALS = "send_credentials"
    REMINDER = "reminder"
    RECHECK_REQUIREMENTS = "recheck_requirements"
    EVENT_START = "event_start"
    EVENT_FINISH = "event_finish"
    PURGE_CREDENTIALS = "purge_credentials"
    BROADCAST = "broadcast"
    MEMBERSHIP_RECHECK = "membership_recheck"
    CHANNEL_ACCESS_RECHECK = "channel_access_recheck"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    PERMANENT_FAIL = "permanent_fail"
    SKIPPED = "skipped"


class ReportReason(StrEnum):
    NO_CREDENTIALS = "no_credentials"
    FAKE_PRIZE = "fake_prize"
    FAKE_ORGANIZER = "fake_organizer"
    WRONG_ROOM = "wrong_room"
    SUDDEN_RULE_CHANGE = "sudden_rule_change"
    UNPAID_PRIZE = "unpaid_prize"
    CHEATER = "cheater"
    INAPPROPRIATE = "inappropriate"
    OTHER = "other"


class ReportStatus(StrEnum):
    NEW = "new"
    IN_REVIEW = "in_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CLOSED = "closed"


class BroadcastStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    CANCELLED = "cancelled"


class NotificationKind(StrEnum):
    EVENT_PUBLISHED = "event_published"
    REMINDER = "reminder"
    REQUIREMENTS_INCOMPLETE = "requirements_incomplete"
    REGISTRATION_CONFIRMED = "registration_confirmed"
    EVENT_FULL = "event_full"
    WAITLIST_PROMOTED = "waitlist_promoted"
    EVENT_CHANGED = "event_changed"
    EVENT_CANCELLED = "event_cancelled"
    ROOM_CREDENTIALS = "room_credentials"
    RESULT = "result"
    ADMIN = "admin"
    SECURITY = "security"


class GlobalChannelScope(StrEnum):
    ALL = "all"
    PLAYER = "player"
    ORGANIZER = "organizer"


class TrustEventType(StrEnum):
    SUCCESSFUL_EVENT = "successful_event"
    CONFIRMED_REPORT = "confirmed_report"
    CANCELLATION = "cancellation"
    CREDENTIAL_ACCURACY = "credential_accuracy"
    PRIZE_PROOF = "prize_proof"
    ACTIVITY_DURATION = "activity_duration"
    PLAYER_REVIEW = "player_review"
    CHANNEL_VERIFIED = "channel_verified"


class PrizePaidVote(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class DeepLinkKind(StrEnum):
    EVENT = "event"
    REFERRAL = "ref"
    ORGANIZER = "org"
    START = "start"


class AnnouncementStatus(StrEnum):
    PUBLISHED = "published"
    HIDDEN = "hidden"
    DELETED = "deleted"
