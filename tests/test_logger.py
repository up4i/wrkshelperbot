import pytest
from action_logger import build_log_message

def test_ban_message_with_duration():
    msg = build_log_message(
        action="ban",
        target_id=123,
        target_name="@spammer",
        admin_name="@ogkush",
        group_id=-100456,
        group_name="My Group",
        reason="spamming",
        duration_secs=604800,
    )
    assert "🔨 Ban" in msg
    assert "@spammer" in msg
    assert "123" in msg
    assert "@ogkush" in msg
    assert "spamming" in msg
    assert "7d" in msg
    assert "My Group" in msg

def test_mute_message_permanent():
    msg = build_log_message(
        action="mute",
        target_id=456,
        target_name="@baduser",
        admin_name="@admin",
        group_id=-100789,
        group_name="Test Group",
        reason=None,
        duration_secs=None,
    )
    assert "🔇 Mute" in msg
    assert "Duration" not in msg

def test_warn_message_includes_count():
    msg = build_log_message(
        action="warn",
        target_id=789,
        target_name="@user",
        admin_name="@admin",
        group_id=-100111,
        group_name="Group",
        reason="rulebreak",
        warn_count=2,
        warn_limit=3,
    )
    assert "⚠️ Warn 2/3" in msg

def test_kick_message():
    msg = build_log_message(
        action="kick",
        target_id=111,
        target_name="@kicked",
        admin_name="@admin",
        group_id=-100222,
        group_name="Group",
        reason="bye",
    )
    assert "👢 Kick" in msg
