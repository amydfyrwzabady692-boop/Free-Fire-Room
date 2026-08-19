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


def _center(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill, *, max_w: int | None = None) -> int:
    shown = _fa(text)
    tw = _text_w(draw, shown, font)
    if max_w and tw > max_w:
        while shown and _text_w(draw, shown + "…", font) > max_w:
            shown = shown[:-1]
        shown = shown + "…"
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
    if len(lines) == limit and words:
        rest = " ".join(words[len(" ".join(lines).split()) :])
        if rest:
            last = lines[-1]
            while last and _text_w(draw, _fa(last + "…"), font) > max_w:
                last = last[:-1]
            lines[-1] = last + "…"
    return lines or [text]


def _card(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], *, fill, outline=None, width: int = 3) -> None:
    draw.rounded_rectangle(xy, radius=36, fill=fill, outline=outline, width=width)


def render_event_poster(
    *,
    prize: str,
    when: str,
    host: str,
    channels: int,
    bot_username: str = "",
) -> bytes:
    img = _gradient()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle((48, 48, W - 48, H - 48), radius=48, outline=(*ORANGE, 210), width=6)
    d.rounded_rectangle((70, 70, W - 70, 210), radius=28, fill=(255, 122, 24, 42))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    y = 92
    y = _center(draw, y, "FREE FIRE CUSTOM", _bold(28), GOLD) + 8
    y = _center(draw, y, "کاستوم جایزه‌دار", _bold(52), WHITE) + 28

    _card(draw, (90, 250, W - 90, 720), fill=(16, 24, 48), outline=GOLD, width=3)
    y = 290
    y = _center(draw, y, "جایزه", _reg(32), CYAN) + 18
    prize_font = _bold(64 if len(prize or "") < 22 else 48)
    for line in _wrap(draw, prize or "جایزه کاستوم", prize_font, W - 220, 3):
        y = _center(draw, y, line, prize_font, GOLD, max_w=W - 240) + 10

    _card(draw, (90, 760, W - 90, 1120), fill=(14, 20, 40), outline=(60, 80, 120), width=2)
    y = 800
    y = _center(draw, y, "ساعت کاستوم", _reg(28), MUTED) + 12
    y = _center(draw, y, when, _bold(40), WHITE, max_w=W - 220) + 28
    host_line = f"برگزارکننده: {host or '—'}"
    y = _center(draw, y, host_line, _reg(32), CYAN, max_w=W - 220) + 18
    ch_line = f"کانال جوین اجباری: {to_fa_digits(str(channels))} مورد"
    _center(draw, y, ch_line, _reg(30), MUTED, max_w=W - 220)

    handle = (bot_username or "").lstrip("@")
    if not handle:
        try:
            from app.core.config import get_settings

            handle = (get_settings().bot_username or "").lstrip("@")
        except Exception:
            handle = ""
    footer = "این بنر را در کانال بگذارید · جوین از لینک ربات"
    if handle:
        footer = f"t.me/{handle}  ·  جوین کن  ·  سر ساعت آیدی و رمز داخل ربات"
    _center(draw, 1185, footer, _reg(26), GOLD, max_w=W - 120)
    _center(draw, 1240, "رمز در گروه نیست — فقط پیام خصوصی ربات", _reg(24), MUTED, max_w=W - 120)

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


def event_poster_bytes(event, *, channels: int = 0) -> bytes:
    prize = (getattr(event, "prize_summary", None) or getattr(event, "title", None) or "کاستوم جایزه‌دار").strip()
    org = getattr(event, "organizer", None)
    host = (org.display_name if org and org.display_name else None) or "برگزارکننده"
    when = format_local(event.starts_at, getattr(event, "timezone", None) or "Asia/Tehran")
    return render_event_poster(prize=prize, when=when, host=host, channels=channels)


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
