from app.services.registration import merge_registration_source


def test_deep_link_wins_over_bot_and_recheck():
    assert merge_registration_source("bot", "deep_link") == "deep_link"
    assert merge_registration_source("deep_link", "recheck") == "deep_link"
    assert merge_registration_source("deep_link", "rules") == "deep_link"
    assert merge_registration_source(None, "deep_link") == "deep_link"


def test_recheck_does_not_wipe_existing_source():
    assert merge_registration_source("bot", "recheck") == "bot"
    assert merge_registration_source("bot", "rules") == "bot"
    assert merge_registration_source("bot", None) == "bot"


def test_first_real_source_is_kept():
    assert merge_registration_source(None, "bot") == "bot"
    assert merge_registration_source(None, "recheck") == "recheck"


def test_audit_actor_telegram_id_is_bigint():
    from sqlalchemy import BigInteger

    from app.models.admin import AuditLog

    col = AuditLog.__table__.c.actor_telegram_id
    assert isinstance(col.type, BigInteger)


def test_menu_labels_match_with_or_without_icons():
    from app.bot.keyboards.common import labeled, unpaint

    names = labeled("کاستوم‌های جایزه‌دار", "بازگشت")
    assert "کاستوم‌های جایزه‌دار" in names
    assert "🟢 کاستوم‌های جایزه‌دار" in names
    assert "🟢کاستوم‌های جایزه‌دار" in names
    assert unpaint("🔵 پنل برگزارکننده") == "پنل برگزارکننده"
    assert unpaint("کاستوم‌های امروز") == "کاستوم‌های امروز"
