import logging
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import is_admin

log = logging.getLogger(__name__)

_DEFAULT_WELCOME = "👋 Welcome to {group}, {mention}!"
_DEFAULT_GOODBYE = "👋 {name} has left the group."


def _render(template: str, user, chat) -> str:
    mention = f"[{user.full_name}](tg://user?id={user.id})"
    return (
        template
        .replace("{name}", user.full_name)
        .replace("{id}", str(user.id))
        .replace("{mention}", mention)
        .replace("{username}", f"@{user.username}" if user.username else user.full_name)
        .replace("{group}", chat.title or str(chat.id))
    )


async def on_new_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.new_chat_members:
        return
    chat = msg.chat
    group = await db.get_group(config.DB_PATH, chat.id)
    if not group or not group.get("welcome_enabled", 1):
        return
    template = group.get("welcome_text") or _DEFAULT_WELCOME
    for user in msg.new_chat_members:
        if user.is_bot:
            continue
        text = _render(template, user, chat)
        try:
            await ctx.bot.send_message(chat.id, text, parse_mode="Markdown")
        except TelegramError:
            try:
                await ctx.bot.send_message(chat.id, text)
            except TelegramError:
                pass


async def on_member_left(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.left_chat_member:
        return
    user = msg.left_chat_member
    if user.is_bot:
        return
    chat = msg.chat
    group = await db.get_group(config.DB_PATH, chat.id)
    if not group or not group.get("goodbye_enabled", 1):
        return
    template = group.get("goodbye_text") or _DEFAULT_GOODBYE
    text = _render(template, user, chat)
    try:
        await ctx.bot.send_message(chat.id, text, parse_mode="Markdown")
    except TelegramError:
        try:
            await ctx.bot.send_message(chat.id, text)
        except TelegramError:
            pass


async def cmd_setwelcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if ctx.args and ctx.args[0].lower() == "off":
        await db.upsert_group(config.DB_PATH, chat_id)
        await db.update_group(config.DB_PATH, chat_id, welcome_enabled=0)
        await msg.reply_text("✅ Welcome messages disabled.")
        return

    text = " ".join(ctx.args) if ctx.args else None
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or msg.reply_to_message.caption

    if not text:
        await msg.reply_text(
            "Usage: `/setwelcome <text>` or reply to a message.\n"
            "Variables: `{name}` `{id}` `{mention}` `{username}` `{group}`\n"
            "Disable with `/setwelcome off`.",
            parse_mode="Markdown",
        )
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, welcome_text=text, welcome_enabled=1)
    await msg.reply_text(f"✅ Welcome message set:\n\n{text}")


async def cmd_setgoodbye(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if ctx.args and ctx.args[0].lower() == "off":
        await db.upsert_group(config.DB_PATH, chat_id)
        await db.update_group(config.DB_PATH, chat_id, goodbye_enabled=0)
        await msg.reply_text("✅ Goodbye messages disabled.")
        return

    text = " ".join(ctx.args) if ctx.args else None
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or msg.reply_to_message.caption

    if not text:
        await msg.reply_text(
            "Usage: `/setgoodbye <text>` or reply to a message.\n"
            "Variables: `{name}` `{id}` `{mention}` `{username}` `{group}`\n"
            "Disable with `/setgoodbye off`.",
            parse_mode="Markdown",
        )
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, goodbye_text=text, goodbye_enabled=1)
    await msg.reply_text(f"✅ Goodbye message set:\n\n{text}")


async def cmd_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    group = await db.get_group(config.DB_PATH, msg.chat.id)
    enabled = group.get("welcome_enabled", 1) if group else 1
    template = (group.get("welcome_text") if group else None) or _DEFAULT_WELCOME
    status = "✅ enabled" if enabled else "❌ disabled"
    await msg.reply_text(
        f"👋 *Welcome message* — {status}\n\n`{template}`",
        parse_mode="Markdown",
    )


async def cmd_goodbye(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    group = await db.get_group(config.DB_PATH, msg.chat.id)
    enabled = group.get("goodbye_enabled", 1) if group else 1
    template = (group.get("goodbye_text") if group else None) or _DEFAULT_GOODBYE
    status = "✅ enabled" if enabled else "❌ disabled"
    await msg.reply_text(
        f"👋 *Goodbye message* — {status}\n\n`{template}`",
        parse_mode="Markdown",
    )
