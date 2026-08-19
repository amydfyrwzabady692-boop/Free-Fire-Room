"""Shared copy and Telegram emoji (Premium clients animate these)."""

SEP = "━━━━━━━━━━━━━━"

FIRE = "🔥"
GEM = "💎"
GAME = "🎮"
GIFT = "🎁"
CLOCK = "🕐"
WAIT = "⏳"
USER = "👤"
CH = "📢"
STAR = "⭐"
ID = "🆔"
LOCK = "🔐"
OK = "✅"
NO = "❌"
WARN = "⚠️"
CROWN = "👑"
SPARK = "✨"
TROPHY = "🏆"
LINK = "🔗"
BELL = "🔔"
SHIELD = "🛡"
MEMO = "📝"
KEY = "🔑"
WAVE = "👋"
TARGET = "🎯"
SIREN = "🚨"
MEDAL = "🏅"
DOOR = "🚪"
CHECK = "🟢"

ROOM_ID = "ROOM ID"
PASS = "PASS"


def room_pair(room_id: str, password: str) -> str:
    return (
        f"{ID} <b>{ROOM_ID}</b>\n<code>{room_id}</code>\n\n"
        f"{LOCK} <b>{PASS}</b>\n<code>{password}</code>"
    )
