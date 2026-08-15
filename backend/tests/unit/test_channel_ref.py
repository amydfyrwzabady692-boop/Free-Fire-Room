from app.bot.helpers import parse_channel_ref_text


def test_parse_channel_username():
    assert parse_channel_ref_text("@mychannel") == "@mychannel"
    assert parse_channel_ref_text("mychannel") == "@mychannel"
    assert parse_channel_ref_text("https://t.me/mychannel") == "@mychannel"
    assert parse_channel_ref_text("https://t.me/mychannel/12") == "@mychannel"


def test_parse_channel_numeric_and_invite():
    assert parse_channel_ref_text("-1001234567890") == -1001234567890
    assert parse_channel_ref_text("https://t.me/+AbCdEf") == "https://t.me/+AbCdEf"
    assert parse_channel_ref_text("-") is None
    assert parse_channel_ref_text("") is None
    assert parse_channel_ref_text("two words") is None
