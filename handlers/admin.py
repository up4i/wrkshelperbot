import logging
from telegram import Update
from telegram.ext import ContextTypes

import config
import db
from utils import is_admin

log = logging.getLogger(__name__)


async def on_any_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Auto-register new groups with default settings on first message."""
    chat_id = update.effective_chat.id
    await db.upsert_group(config.DB_PATH, chat_id)


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    lines = [f"👤 Your ID: `{update.effective_user.id}`", f"💬 Chat ID: `{msg.chat.id}`"]
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        lines.append(f"🎯 Replied user ID: `{u.id}`")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    target_msg = msg.reply_to_message or msg
    user = target_msg.from_user
    if not user:
        await msg.reply_text("No user found.")
        return
    name = f"@{user.username}" if user.username else user.full_name
    await msg.reply_text(
        f"👤 *User Info*\n\nName: {name}\nID: `{user.id}`\nBot: {'yes' if user.is_bot else 'no'}",
        parse_mode="Markdown",
    )


async def cmd_setlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not ctx.args:
        await msg.reply_text("Usage: `/setlog @channelname`", parse_mode="Markdown")
        return

    channel = ctx.args[0]
    try:
        chat = await ctx.bot.get_chat(channel)
        channel_id = chat.id
    except Exception as e:
        await msg.reply_text(f"Couldn't find that channel: {e}")
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, log_channel_id=channel_id)
    await msg.reply_text(f"✅ Log channel set to `{channel}` (`{channel_id}`).", parse_mode="Markdown")
