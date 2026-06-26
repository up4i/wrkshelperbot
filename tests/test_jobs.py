import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_sweep_lifts_expired_mute():
    from jobs import sweep_punishments

    expired = [{"id": 1, "chat_id": -100111, "user_id": 999, "action": "mute", "expires_at": int(time.time()) - 10}]

    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.bot.restrict_chat_member = AsyncMock()

    with patch("jobs.db.get_expired_punishments", AsyncMock(return_value=expired)), \
         patch("jobs.db.delete_punishment_by_id", AsyncMock()) as mock_delete, \
         patch("jobs.config.DB_PATH", ":memory:"):
        await sweep_punishments(ctx)

    ctx.bot.restrict_chat_member.assert_called_once()
    mock_delete.assert_called_once_with(":memory:", 1)

@pytest.mark.asyncio
async def test_sweep_lifts_expired_ban():
    from jobs import sweep_punishments

    expired = [{"id": 2, "chat_id": -100111, "user_id": 888, "action": "ban", "expires_at": int(time.time()) - 5}]

    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.bot.unban_chat_member = AsyncMock()

    with patch("jobs.db.get_expired_punishments", AsyncMock(return_value=expired)), \
         patch("jobs.db.delete_punishment_by_id", AsyncMock()) as mock_delete, \
         patch("jobs.config.DB_PATH", ":memory:"):
        await sweep_punishments(ctx)

    ctx.bot.unban_chat_member.assert_called_once_with(-100111, 888, only_if_banned=True)
    mock_delete.assert_called_once_with(":memory:", 2)

@pytest.mark.asyncio
async def test_sweep_skips_on_telegram_error():
    from jobs import sweep_punishments
    from telegram.error import TelegramError

    expired = [{"id": 3, "chat_id": -100111, "user_id": 777, "action": "mute", "expires_at": int(time.time()) - 1}]

    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.bot.restrict_chat_member = AsyncMock(side_effect=TelegramError("no perms"))

    with patch("jobs.db.get_expired_punishments", AsyncMock(return_value=expired)), \
         patch("jobs.db.delete_punishment_by_id", AsyncMock()) as mock_delete, \
         patch("jobs.config.DB_PATH", ":memory:"):
        await sweep_punishments(ctx)

    mock_delete.assert_called_once()
