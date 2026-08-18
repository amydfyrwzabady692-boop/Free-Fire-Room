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
