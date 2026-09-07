from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.core.time import format_local, to_fa_digits

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
W, H = 1080, 1350

ORANGE = (255, 122, 24)
GOLD = (255, 196, 72)
CYAN = (80, 214, 255)
WHITE = (248, 250, 255)
MUTED = (176, 186, 204)
NAVY = (10, 16, 36)
GREEN = (86, 224, 154)

#: the same words as the inline button under the post, so the picture and the
#: button never say two different things
CTA = "ورود به کاستوم جایزه دار"
#: no medal emoji here on purpose - Vazirmatn carries no emoji glyphs and Pillow
#: drops them silently, so the podium is spelled out. The caption below the
#: photo is where 🥇🥈🥉 render, because Telegram draws that text itself.
PLACE_NAMES = ("نفر اول", "نفر دوم", "نفر سوم")

TIME_CARD_H = 276
RULES_CARD_H = 136
PILL_H = 92


def _fa(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(raw))
    except Exception:
        return raw


def _font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = FONTS_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _bold(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _font("Vazirmatn-Bold.ttf", size)


def _reg(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _font("Vazirmatn-Regular.ttf", size)


def _gradient() -> Image.Image:
    img = Image.new("RGB", (W, H), NAVY)
    layer = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(layer)
    d.rectangle((0, 0, W, 300), fill=(48, 22, 8))
    d.polygon([(0, 240), (W, 160), (W, 340), (0, 420)], fill=(72, 32, 8))
    d.ellipse((-220, -180, 520, 460), fill=(120, 48, 10))
    d.ellipse((620, 940, 1320, 1580), fill=(10, 56, 102))
    d.rectangle((0, 1140, W, H), fill=(22, 12, 8))
    layer = layer.filter(ImageFilter.GaussianBlur(28))
    return Image.blend(img, layer, 0.62)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit(draw: ImageDraw.ImageDraw, raw: str, font, max_w: int) -> str:
    """Shorten the LOGICAL string until its shaped form fits.

    Trimming after reshaping would eat the wrong end: bidi has already put the
    visually-last glyph at what is logically the start of a Persian sentence,
    so slicing the shaped string removes the first word, not the last.
    """
    text = raw or ""
    if _text_w(draw, _fa(text), font) <= max_w:
        return text
    while text and _text_w(draw, _fa(text + "…"), font) > max_w:
        text = text[:-1]
    return (text + "…") if text else ""


def _center(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill, *, max_w: int | None = None) -> int:
    shown = _fa(_fit(draw, text, font, max_w) if max_w else text)
    if not shown:
        return y
    tw = _text_w(draw, shown, font)
    x = (W - tw) // 2
    draw.text((x, y), shown, font=font, fill=fill)
    box = draw.textbbox((x, y), shown, font=font)
    return box[3]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, limit: int = 3) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if _text_w(draw, _fa(trial), font) <= max_w or not cur:
            cur = trial
            continue
        lines.append(cur)
        cur = word
        if len(lines) >= limit:
            break
    if cur and len(lines) < limit:
        lines.append(cur)
    elif cur and lines:
        # the overflow word has nowhere to go: mark the last line as cut
        lines[-1] = _fit(draw, lines[-1] + " " + cur, font, max_w)
    return lines or [text]


def _card(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], *, fill, outline=None, width: int = 3) -> None:
    draw.rounded_rectangle(xy, radius=36, fill=fill, outline=outline, width=width)


def _pill(draw: ImageDraw.ImageDraw, y: int, text: str, *, fill, text_fill, font, pad: int = 34) -> int:
    """A filled lozenge sized to its own text - the call to action."""
    shown = _fa(_fit(draw, text, font, W - 260))
    tw = _text_w(draw, shown, font)
    box = draw.textbbox((0, 0), shown, font=font)
    th = box[3] - box[1]
    x0 = (W - tw) // 2 - pad
    x1 = (W + tw) // 2 + pad
    draw.rounded_rectangle((x0, y, x1, y + th + pad), radius=(th + pad) // 2, fill=fill)
    draw.text(((W - tw) // 2, y + pad // 2 - box[1]), shown, font=font, fill=text_fill)
    return y + th + pad


def render_event_poster(
    *,
    prize: str = "",
    when: str = "",
    host: str = "",
    channels: int = 0,
    bot_username: str = "",
    places: list[str] | None = None,
    channel_name: str = "",
    social_pages: int = 0,
) -> bytes:
    """The whole custom on one image, in the order a player reads it.

    The layout flows: every block measures itself and advances a cursor, so a
    three-line prize pushes the time card down instead of colliding with it.
    """
    img = _gradient()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle((44, 44, W - 44, H - 44), radius=48, outline=(*ORANGE, 210), width=6)
    d.rounded_rectangle((66, 66, W - 66, 214), radius=28, fill=(255, 122, 24, 42))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    y = 88
    y = _center(draw, y, "FREE FIRE CUSTOM", _bold(28), GOLD) + 10
    y = _center(draw, y, "کاستوم جایزه‌دار", _bold(54), WHITE) + 30

    rows = [line for line in (places or []) if line] or (
        [" ".join((prize or "").split())] if prize else []
    )
    rows = rows[:3]
    prize_font = _bold(44 if max((len(r) for r in rows), default=0) > 24 else 54)
    prize_h = 108 + max(1, len(rows)) * 74
    # measure the whole stack first, then centre it: a one-prize custom would
    # otherwise leave a hole where two more lines would have been
    stack = prize_h + 24 + TIME_CARD_H + 22 + RULES_CARD_H + 24 + PILL_H + 46
    top, bottom = y, H - 130
    y = top + max(0, (bottom - top - stack) // 2)

    _card(draw, (84, y, W - 84, y + prize_h), fill=(16, 24, 48), outline=GOLD, width=3)
    inner = y + 34
    inner = _center(draw, inner, "جایزه", _reg(32), CYAN) + 16
    if rows:
        for i, line in enumerate(rows):
            label = f"{PLACE_NAMES[i]}: {line}" if len(rows) > 1 else line
            inner = _center(draw, inner, label, prize_font, GOLD, max_w=W - 220) + 14
    else:
        _center(draw, inner, "جایزه کاستوم", prize_font, GOLD, max_w=W - 220)
    y = y + prize_h + 24

    _card(draw, (84, y, W - 84, y + TIME_CARD_H), fill=(14, 20, 40), outline=(60, 80, 120), width=2)
    inner = y + 30
    inner = _center(draw, inner, "زمان کاستوم", _reg(28), MUTED) + 10
    inner = _center(draw, inner, when, _bold(42), WHITE, max_w=W - 200) + 20
    inner = _center(draw, inner, f"برگزارکننده: {host or '—'}", _reg(30), CYAN, max_w=W - 200) + 10
    where = channel_name or (f"{to_fa_digits(str(channels))} کانال جوین اجباری" if channels else "")
    if where:
        _center(draw, inner, where, _reg(28), MUTED, max_w=W - 200)
    y = y + TIME_CARD_H + 22

    conditions = ["چیت و تبانی ممنوع"]
    if channels:
        conditions.append(f"عضویت در {to_fa_digits(str(channels))} کانال")
    if social_pages:
        conditions.append(f"فالو {to_fa_digits(str(social_pages))} پیج + اسکرین")
    _card(draw, (84, y, W - 84, y + RULES_CARD_H), fill=(20, 14, 10), outline=(150, 60, 20), width=2)
    inner = y + 22
    inner = _center(draw, inner, "شرایط و قوانین", _reg(28), ORANGE) + 10
    _center(draw, inner, " · ".join(conditions), _reg(30), WHITE, max_w=W - 200)
    y = y + RULES_CARD_H + 24

    y = _pill(draw, y, CTA, fill=GREEN, text_fill=(8, 26, 18), font=_bold(38)) + 14

    handle = (bot_username or "").lstrip("@")
    if not handle:
        try:
            from app.core.config import get_settings

            handle = (get_settings().bot_username or "").lstrip("@")
        except Exception:
            handle = ""
    if handle:
        _center(draw, y, f"t.me/{handle}", _reg(28), GOLD, max_w=W - 160)
    _center(
        draw,
        H - 104,
        "ROOM ID و PASS در گروه نیست — فقط پیام خصوصی ربات",
        _reg(24),
        MUTED,
        max_w=W - 140,
    )

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_digest_poster(*, date_label: str, items: list[dict]) -> bytes:
    img = _gradient()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle((48, 48, W - 48, H - 48), radius=48, outline=(*ORANGE, 210), width=6)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    y = 88
    y = _center(draw, y, "FREE FIRE CUSTOM", _bold(26), GOLD) + 6
    y = _center(draw, y, "کاستوم‌های جایزه‌دار پیش‌رو", _bold(44), WHITE, max_w=W - 140) + 8
    y = _center(draw, y, date_label, _reg(28), CYAN) + 24

    top = y + 10
    rows = items[:5] or [{"prize": "فعلاً کاستومی نیست", "when": ""}]
    gap = 18
    box_h = min(150, max(118, int((1080 - top) / max(len(rows), 1) - gap)))
    for i, item in enumerate(rows):
        y0 = top + i * (box_h + gap)
        _card(draw, (90, y0, W - 90, y0 + box_h), fill=(16, 24, 48), outline=(255, 122, 24) if i == 0 else (70, 90, 130), width=3)
        idx = to_fa_digits(str(i + 1))
        prize = (item.get("prize") or item.get("title") or "کاستوم").replace("\n", " ")
        when = item.get("when") or ""
        _center(draw, y0 + 24, f"{idx}  ·  {prize}", _bold(34), GOLD, max_w=W - 240)
        if when:
            _center(draw, y0 + 78, f"ساعت {when}", _reg(26), WHITE, max_w=W - 240)

    _center(draw, 1228, "یکی را باز کنید · کانال‌ها را جوین کنید · عضو شدم", _reg(26), GOLD, max_w=W - 120)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def event_poster_bytes(event, *, channels: int = 0, social_pages: int = 0) -> bytes:
    from app.services.event_display import (
        channel_public_label,
        organizer_public_name,
        prize_places,
        required_channel_count,
        resolve_event_channel,
    )

    places = prize_places(event)
    host = organizer_public_name(getattr(event, "organizer", None))
    tz = getattr(event, "timezone", None) or "Asia/Tehran"
    when = format_local(event.starts_at, tz)
    try:
        channel_name = channel_public_label(resolve_event_channel(event))
    except Exception:  # noqa: BLE001
        channel_name = ""
    return render_event_poster(
        prize=(getattr(event, "prize_summary", None) or "").strip(),
        places=places,
        when=when,
        host=host,
        channels=channels or required_channel_count(event),
        channel_name=channel_name,
        social_pages=social_pages,
    )


def digest_poster_bytes(events: list) -> bytes:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    date_label = format_local(now, "Asia/Tehran")
    items = []
    for event in events[:5]:
        prize = (event.prize_summary or event.title or "کاستوم").strip().replace("\n", " ")
        items.append(
            {
                "prize": prize,
                "when": format_local(event.starts_at, event.timezone, compact=True),
            }
        )
    return render_digest_poster(date_label=date_label, items=items)


def as_input_file(png: bytes, name: str = "custom-banner.png"):
    from aiogram.types import BufferedInputFile

    return BufferedInputFile(png, filename=name)
