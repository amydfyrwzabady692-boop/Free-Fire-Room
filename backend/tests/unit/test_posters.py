from app.services.posters import render_digest_poster, render_event_poster


def test_event_poster_is_png():
    png = render_event_poster(
        prize="۱۰۰۰ الماس",
        when="سه‌شنبه ۲۸ مرداد ۱۴۰۵ — ۲۲:۰۰",
        host="علی",
        channels=2,
        bot_username="FFCustom_bot",
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 20_000


def test_digest_poster_is_png():
    png = render_digest_poster(
        date_label="سه‌شنبه ۲۸ مرداد",
        items=[
            {"prize": "۱۰۰۰ الماس", "when": "۲۲:۰۰"},
            {"prize": "اسکین", "when": "۲۳:۳۰"},
        ],
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 20_000
