import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Chat, Message, User, ChatMember, ChatPermissions

def make_user(uid=111, username="admin"):
    u = MagicMock(spec=User)
    u.id = uid
    u.username = username
    u.full_name = username
    return u

def make_message(text="/mute", chat_id=-100123, from_user=None, reply_to=None):
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = 1
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = chat_id
    msg.chat.title = "Test Group"
    msg.from_user = from_user or make_user()
    msg.reply_to_message = reply_to
    msg.reply_text = AsyncMock()
    msg.delete = AsyncMock()
    return msg

def make_update(msg):
    update = MagicMock()
    update.effective_message = msg
    update.effective_user = msg.from_user
    update.effective_chat = msg.chat
    return update

def make_context(bot=None, args=None):
    ctx = MagicMock()
    ctx.bot = bot or AsyncMock()
    ctx.bot.get_chat_member = AsyncMock()
    ctx.bot.restrict_chat_member = AsyncMock()
    ctx.bot.promote_chat_member = AsyncMock()
    ctx.args = args or []
    return ctx

@pytest.mark.asyncio
async def test_mute_requires_reply():
    from handlers.moderation import cmd_mute
    msg = make_message("/mute")
    msg.reply_to_message = None
    update = make_update(msg)
    ctx = make_context()

    admin_member = MagicMock(spec=ChatMember)
    admin_member.status = "administrator"
    ctx.bot.get_chat_member.return_value = admin_member

    with patch("handlers.moderation.config.DB_PATH", ":memory:"), \
         patch("handlers.moderation.db.upsert_group", AsyncMock()), \
         patch("handlers.moderation.db.get_group", AsyncMock(return_value={"log_channel_id": None, "warn_limit": 3, "warn_action": "mute", "warn_mute_duration": 3600, "default_mute_duration": None})):
        await cmd_mute(update, ctx)

    msg.reply_text.assert_called_once()
    assert "reply" in msg.reply_text.call_args[0][0].lower()
