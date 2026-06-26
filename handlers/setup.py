import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

import config
import db
from utils import format_duration, is_admin

log = logging.getLogger(__name__)

_SETUP_TIMEOUT = 300  # seconds


def _main_menu(group_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Log Channel",   callback_data="setup:log"),
            InlineKeyboardButton("⚠️ Warnings",      callback_data="setup:warns"),
        ],
        [
            InlineKeyboardButton("🔇 Mute Settings", callback_data="setup:mute"),
            InlineKeyboardButton("ℹ️ Status",         callback_data="setup:status"),
        ],
        [InlineKeyboardButton("❌ Close",            callback_data="setup:close")],
    ])


def _warn_menu(warn_limit: int, warn_action: str) -> InlineKeyboardMarkup:
    action_mute_label = "✅ Mute" if warn_action == "mute" else "Mute"
    action_ban_label  = "✅ Ban"  if warn_action == "ban"  else "Ban"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("−", callback_data="setup:wlimit_dec"),
            InlineKeyboardButton(f"Warn limit: {warn_limit}", callback_data="setup:noop"),
            InlineKeyboardButton("+", callback_data="setup:wlimit_inc"),
        ],
        [
            InlineKeyboardButton(action_mute_label, callback_data="setup:waction_mute"),
            InlineKeyboardButton(action_ban_label,  callback_data="setup:waction_ban"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="setup:back")],
    ])


def _mute_menu(default_mute: int | None) -> InlineKeyboardMarkup:
    current = format_duration(default_mute) if default_mute else "Permanent"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("30m", callback_data="setup:mdef_1800"),
            InlineKeyboardButton("1h",  callback_data="setup:mdef_3600"),
            InlineKeyboardButton("6h",  callback_data="setup:mdef_21600"),
            InlineKeyboardButton("1d",  callback_data="setup:mdef_86400"),
        ],
        [InlineKeyboardButton("Permanent", callback_data="setup:mdef_0")],
        [InlineKeyboardButton(f"Current: {current}", callback_data="setup:noop")],
        [InlineKeyboardButton("⬅️ Back", callback_data="setup:back")],
    ])


def _warn_mute_menu(warn_mute_duration: int) -> InlineKeyboardMarkup:
    current = format_duration(warn_mute_duration)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("30m", callback_data="setup:wdur_1800"),
            InlineKeyboardButton("1h",  callback_data="setup:wdur_3600"),
            InlineKeyboardButton("6h",  callback_data="setup:wdur_21600"),
            InlineKeyboardButton("1d",  callback_data="setup:wdur_86400"),
        ],
        [InlineKeyboardButton(f"Current: {current}", callback_data="setup:noop")],
        [InlineKeyboardButton("⬅️ Back", callback_data="setup:back")],
    ])


async def cmd_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        await msg.reply_text("Admins only.")
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group_name = msg.chat.title or str(chat_id)
    sent = await msg.reply_text(
        f"⚙️ *Group Settings — {group_name}*",
        parse_mode="Markdown",
        reply_markup=_main_menu(group_name),
    )
    ctx.job_queue.run_once(
        _timeout_cleanup,
        _SETUP_TIMEOUT,
        data={"chat_id": chat_id, "message_id": sent.message_id},
        name=f"setup_timeout_{chat_id}",
    )


async def _timeout_cleanup(ctx: ContextTypes.DEFAULT_TYPE):
    data = ctx.job.data
    try:
        await ctx.bot.delete_message(data["chat_id"], data["message_id"])
    except TelegramError:
        pass


async def setup_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user_id = query.from_user.id

    if not await is_admin(ctx.bot, chat_id, user_id):
        await query.answer("Admins only.", show_alert=True)
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)
    action = query.data.split(":", 1)[1]

    if action == "close":
        try:
            await query.message.delete()
        except TelegramError:
            pass
        return

    if action == "noop":
        return

    if action == "back":
        await query.edit_message_text(
            f"⚙️ *Group Settings — {query.message.chat.title}*",
            parse_mode="Markdown",
            reply_markup=_main_menu(query.message.chat.title or str(chat_id)),
        )
        return

    if action == "log":
        await query.edit_message_text(
            "📋 *Log Channel*\n\nUse the command:\n`/setlog @yourchannel`\n\nThe bot must be an admin in that channel.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="setup:back")]]),
        )
        return

    if action == "warns":
        await query.edit_message_text(
            "⚠️ *Warning Settings*",
            parse_mode="Markdown",
            reply_markup=_warn_menu(group["warn_limit"], group["warn_action"]),
        )
        return

    if action == "wlimit_dec":
        new_limit = max(1, group["warn_limit"] - 1)
        await db.update_group(config.DB_PATH, chat_id, warn_limit=new_limit)
        await query.edit_message_reply_markup(_warn_menu(new_limit, group["warn_action"]))
        return

    if action == "wlimit_inc":
        new_limit = min(10, group["warn_limit"] + 1)
        await db.update_group(config.DB_PATH, chat_id, warn_limit=new_limit)
        await query.edit_message_reply_markup(_warn_menu(new_limit, group["warn_action"]))
        return

    if action == "waction_mute":
        await db.update_group(config.DB_PATH, chat_id, warn_action="mute")
        await query.edit_message_reply_markup(_warn_menu(group["warn_limit"], "mute"))
        return

    if action == "waction_ban":
        await db.update_group(config.DB_PATH, chat_id, warn_action="ban")
        await query.edit_message_reply_markup(_warn_menu(group["warn_limit"], "ban"))
        return

    if action == "mute":
        await query.edit_message_text(
            "🔇 *Default Mute Duration*\n\nApplied when `/mute` is used with no duration.",
            parse_mode="Markdown",
            reply_markup=_mute_menu(group.get("default_mute_duration")),
        )
        return

    if action.startswith("mdef_"):
        secs = int(action.split("_")[1])
        await db.update_group(config.DB_PATH, chat_id, default_mute_duration=secs if secs else None)
        await query.answer(f"Default mute set to {format_duration(secs) if secs else 'permanent'}.", show_alert=True)
        return

    if action.startswith("wdur_"):
        secs = int(action.split("_")[1])
        await db.update_group(config.DB_PATH, chat_id, warn_mute_duration=secs)
        await query.answer(f"Warn auto-mute duration set to {format_duration(secs)}.", show_alert=True)
        return

    if action == "status":
        log_ch = group.get("log_channel_id")
        log_str = f"`{log_ch}`" if log_ch else "not set"
        def_mute = group.get("default_mute_duration")
        def_mute_str = format_duration(def_mute) if def_mute else "permanent"
        warn_mute = group.get("warn_mute_duration", 3600)
        text = (
            f"ℹ️ *Current Config*\n\n"
            f"Log channel: {log_str}\n"
            f"Warn limit: {group['warn_limit']}\n"
            f"Action at limit: {group['warn_action']}\n"
            f"Auto-mute duration: {format_duration(warn_mute)}\n"
            f"Default /mute duration: {def_mute_str}"
        )
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="setup:back")]]),
        )
        return
