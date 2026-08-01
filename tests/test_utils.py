from utils import display_name, parse_amount
from emojis import badge_markup, badges_markup

def test_parse_amount_short_forms_and_commas():
    assert parse_amount("50k") == 50_000
    assert parse_amount("2.5M") == 2_500_000
    assert parse_amount("1,250") == 1_250
    assert parse_amount("1b") == 1_000_000_000

def test_parse_amount_validation_and_signed_delta():
    assert parse_amount("-50k") is None
    assert parse_amount("-50k", allow_negative=True) == -50_000
    assert parse_amount("all") is None
    assert parse_amount("12x") is None

def test_display_name_with_username():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.username = "ogkush"
    user.full_name = "Bryce"
    assert display_name(user) == "@ogkush"

def test_display_name_without_username():
    from unittest.mock import MagicMock
    user = MagicMock()
    user.username = None
    user.full_name = "Bryce"
    assert display_name(user) == "Bryce"


def test_dynamic_whale_badges_have_telegram_profile_labels():
    assert badge_markup("whale:UTYA") == "🐋 UTYA Whale"
    assert badges_markup(["whale:UTYA", "unknown"]) == "🐋 UTYA Whale"
