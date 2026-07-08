import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

import config
import db
from utils import format_duration, is_admin

log = logging.getLogger(__name__)

_SETUP_TIMEOUT = 300


# ── Menu builders (prefix param lets the same menus work in group or DM) ───────

def _main_menu(group_name: str, prefix: str = "setup") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Log Channel",   callback_data=f"{prefix}:log"),
            InlineKeyboardButton("⚠️ Warnings",      callback_data=f"{prefix}:warns"),
        ],
        [
            InlineKeyboardButton("🔇 Mute Settings", callback_data=f"{prefix}:mute"),
            InlineKeyboardButton("ℹ️ Status",         callback_data=f"{prefix}:status"),
        ],
        [InlineKeyboardButton("❌ Close",            callback_data=f"{prefix}:close")],
    ])


def _warn_menu(warn_limit: int, warn_action: str, prefix: str = "setup") -> InlineKeyboardMarkup:
    ml = "✅ Mute" if warn_action == "mute" else "Mute"
    bl = "✅ Ban"  if warn_action == "ban"  else "Ban"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("−", callback_data=f"{prefix}:wlimit_dec"),
            InlineKeyboardButton(f"Warn limit: {warn_limit}", callback_data=f"{prefix}:noop"),
            InlineKeyboardButton("+", callback_data=f"{prefix}:wlimit_inc"),
        ],
        [
            InlineKeyboardButton(ml, callback_data=f"{prefix}:waction_mute"),
            InlineKeyboardButton(bl, callback_data=f"{prefix}:waction_ban"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:back")],
    ])


def _mute_menu(default_mute: int | None, prefix: str = "setup") -> InlineKeyboardMarkup:
    current = format_duration(default_mute) if default_mute else "Permanent"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("30m", callback_data=f"{prefix}:mdef_1800"),
            InlineKeyboardButton("1h",  callback_data=f"{prefix}:mdef_3600"),
            InlineKeyboardButton("6h",  callback_data=f"{prefix}:mdef_21600"),
            InlineKeyboardButton("1d",  callback_data=f"{prefix}:mdef_86400"),
        ],
        [InlineKeyboardButton("Permanent", callback_data=f"{prefix}:mdef_0")],
        [InlineKeyboardButton(f"Current: {current}", callback_data=f"{prefix}:noop")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:back")],
    ])


def _warn_mute_menu(warn_mute_duration: int, prefix: str = "setup") -> InlineKeyboardMarkup:
    current = format_duration(warn_mute_duration)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("30m", callback_data=f"{prefix}:wdur_1800"),
            InlineKeyboardButton("1h",  callback_data=f"{prefix}:wdur_3600"),
            InlineKeyboardButton("6h",  callback_data=f"{prefix}:wdur_21600"),
            InlineKeyboardButton("1d",  callback_data=f"{prefix}:wdur_86400"),
        ],
        [InlineKeyboardButton(f"Current: {current}", callback_data=f"{prefix}:noop")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:back")],
    ])


# ── Shared action handler ───────────────────────────────────────────────────────

async def _setup_action(
    query, group_id: int, action: str, prefix: str, group_name: str, ctx
) -> None:
    await db.upsert_group(config.DB_PATH, group_id)
    group = await db.get_group(config.DB_PATH, group_id)

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
            f"⚙️ *Group Settings — {group_name}*",
            parse_mode="Markdown",
            reply_markup=_main_menu(group_name, prefix),
        )
        return

    if action == "log":
        is_dm = query.message.chat.type == "private"
        note = "\n\n_Use this command in the group itself._" if is_dm else ""
        await query.edit_message_text(
            f"📋 *Log Channel*\n\nUse the command:\n`/setlog @yourchannel`\n\nThe bot must be an admin in that channel.{note}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:back")]]),
        )
        return

    if action == "warns":
        await query.edit_message_text(
            "⚠️ *Warning Settings*",
            parse_mode="Markdown",
            reply_markup=_warn_menu(group["warn_limit"], group["warn_action"], prefix),
        )
        return

    if action == "wlimit_dec":
        new_limit = max(1, group["warn_limit"] - 1)
        await db.update_group(config.DB_PATH, group_id, warn_limit=new_limit)
        await query.edit_message_reply_markup(_warn_menu(new_limit, group["warn_action"], prefix))
        return

    if action == "wlimit_inc":
        new_limit = min(10, group["warn_limit"] + 1)
        await db.update_group(config.DB_PATH, group_id, warn_limit=new_limit)
        await query.edit_message_reply_markup(_warn_menu(new_limit, group["warn_action"], prefix))
        return

    if action == "waction_mute":
        await db.update_group(config.DB_PATH, group_id, warn_action="mute")
        await query.edit_message_reply_markup(_warn_menu(group["warn_limit"], "mute", prefix))
        return

    if action == "waction_ban":
        await db.update_group(config.DB_PATH, group_id, warn_action="ban")
        await query.edit_message_reply_markup(_warn_menu(group["warn_limit"], "ban", prefix))
        return

    if action == "mute":
        await query.edit_message_text(
            "🔇 *Default Mute Duration*\n\nApplied when `/mute` is used with no duration.",
            parse_mode="Markdown",
            reply_markup=_mute_menu(group.get("default_mute_duration"), prefix),
        )
        return

    if action.startswith("mdef_"):
        secs = int(action.split("_")[1])
        await db.update_group(config.DB_PATH, group_id, default_mute_duration=secs if secs else None)
        await query.answer(
            f"Default mute set to {format_duration(secs) if secs else 'permanent'}.",
            show_alert=True,
        )
        return

    if action.startswith("wdur_"):
        secs = int(action.split("_")[1])
        await db.update_group(config.DB_PATH, group_id, warn_mute_duration=secs)
        await query.answer(f"Warn auto-mute duration set to {format_duration(secs)}.", show_alert=True)
        return

    if action == "status":
        log_ch = group.get("log_channel_id")
        log_str = f"`{log_ch}`" if log_ch else "not set"
        def_mute = group.get("default_mute_duration")
        def_mute_str = format_duration(def_mute) if def_mute else "permanent"
        warn_mute = group.get("warn_mute_duration", 3600)
        text = (
            f"ℹ️ *Current Config — {group_name}*\n\n"
            f"Log channel: {log_str}\n"
            f"Warn limit: {group['warn_limit']}\n"
            f"Action at limit: {group['warn_action']}\n"
            f"Auto-mute duration: {format_duration(warn_mute)}\n"
            f"Default /mute duration: {def_mute_str}"
        )
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:back")]]),
        )
        return


# ── Group setup ─────────────────────────────────────────────────────────────────

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
    if not await is_admin(ctx.bot, chat_id, query.from_user.id):
        await query.answer("Admins only.", show_alert=True)
        return

    action = query.data.split(":", 1)[1]
    group_name = query.message.chat.title or str(chat_id)
    await _setup_action(query, chat_id, action, "setup", group_name, ctx)


# ── DM setup via /connect ───────────────────────────────────────────────────────

async def cmd_connect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⚙️ Configure in DMs →",
            url=f"https://t.me/{ctx.bot.username}?start=setup_{chat_id}",
        )
    ]])
    await msg.reply_text("Click below to configure this group in your DMs.", reply_markup=keyboard)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    if not ctx.args or not ctx.args[0].startswith("setup_"):
        await msg.reply_text("👋 Add me to a group and use /setup or /connect to configure me.")
        return

    try:
        group_id = int(ctx.args[0][6:])
    except ValueError:
        await msg.reply_text("Invalid setup link.")
        return

    if not await is_admin(ctx.bot, group_id, update.effective_user.id):
        await msg.reply_text("You must be an admin in that group to configure it here.")
        return

    try:
        chat = await ctx.bot.get_chat(group_id)
        group_name = chat.title or str(group_id)
    except TelegramError:
        await msg.reply_text("Couldn't reach that group — make sure I'm still in it.")
        return

    await db.upsert_group(config.DB_PATH, group_id)
    prefix = f"dsetup:{group_id}"
    await msg.reply_text(
        f"⚙️ *Configuring: {group_name}*",
        parse_mode="Markdown",
        reply_markup=_main_menu(group_name, prefix),
    )


async def dsetup_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # callback_data format: dsetup:{group_id}:{action}
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    try:
        group_id = int(parts[1])
    except ValueError:
        return
    action = parts[2]

    if not await is_admin(ctx.bot, group_id, query.from_user.id):
        await query.answer("You're no longer an admin in that group.", show_alert=True)
        return

    try:
        chat = await ctx.bot.get_chat(group_id)
        group_name = chat.title or str(group_id)
    except TelegramError:
        group_name = str(group_id)

    await _setup_action(query, group_id, action, f"dsetup:{group_id}", group_name, ctx)
