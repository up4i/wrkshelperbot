import pytest
from utils import parse_duration, format_duration, display_name

def test_parse_duration_minutes():
    assert parse_duration("30m") == 1800

def test_parse_duration_hours():
    assert parse_duration("2h") == 7200

def test_parse_duration_days():
    assert parse_duration("7d") == 604800

def test_parse_duration_invalid():
    assert parse_duration("forever") is None
    assert parse_duration("") is None
    assert parse_duration("1x") is None

def test_parse_duration_case_insensitive():
    assert parse_duration("1H") == 3600

def test_format_duration_days():
    assert format_duration(86400) == "1d"
    assert format_duration(604800) == "7d"

def test_format_duration_hours():
    assert format_duration(3600) == "1h"
    assert format_duration(7200) == "2h"

def test_format_duration_minutes():
    assert format_duration(1800) == "30m"
    assert format_duration(60) == "1m"

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
