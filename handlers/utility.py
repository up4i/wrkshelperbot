import io
import json
import logging
import time
from datetime import datetime, timezone

from telegram import Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import is_admin, display_name

log = logging.getLogger(__name__)


async def cmd_admins(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    admins = await ctx.bot.get_chat_administrators(msg.chat.id)
    lines = ["👮 *Admins*\n"]
    for m in admins:
        if m.user.is_bot:
            continue
        tag = f"@{m.user.username}" if m.user.username else m.user.full_name
        title = f" · _{m.custom_title}_" if m.custom_title else ""
        lines.append(f"• {tag}{title}")
    if len(lines) == 1:
        lines.append("_(no human admins)_")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_setrules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    rules_text = " ".join(ctx.args) if ctx.args else None
    if not rules_text and msg.reply_to_message:
        rules_text = msg.reply_to_message.text or msg.reply_to_message.caption

    if not rules_text:
        await msg.reply_text(
            "Usage: `/setrules <text>` or reply to a message containing the rules.",
            parse_mode="Markdown",
        )
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, rules=rules_text)
    await msg.reply_text("✅ Rules updated.")


async def cmd_rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    group = await db.get_group(config.DB_PATH, chat_id)
    rules = group.get("rules") if group else None

    if not rules:
        await msg.reply_text("No rules set. Admins can use /setrules to set them.")
        return

    text = f"📋 *Rules for {msg.chat.title or 'this group'}*\n\n{rules}"

    # If replying to someone, DM them the rules
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target = msg.reply_to_message.from_user
        try:
            await ctx.bot.send_message(target.id, text, parse_mode="Markdown")
            await msg.reply_text(f"📬 Rules sent to {display_name(target)}'s DMs.")
            return
        except Forbidden:
            pass  # fall through to post in chat

    await msg.reply_text(text, parse_mode="Markdown")


async def cmd_me(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    chat_id = msg.chat.id

    group = await db.get_group(config.DB_PATH, chat_id)
    warn_row = await db.get_warnings(config.DB_PATH, chat_id, user.id)
    warn_limit = group.get("warn_limit", 3) if group else 3
    warn_count = warn_row["count"] if warn_row else 0
    last_reason = warn_row["last_reason"] if warn_row else None

    try:
        member = await ctx.bot.get_chat_member(chat_id, user.id)
        status = member.status
    except TelegramError:
        status = "unknown"

    try:
        member_count = await ctx.bot.get_chat_member_count(chat_id)
        count_line = f"Members: {member_count}\n"
    except TelegramError:
        count_line = ""

    tag = f"@{user.username}" if user.username else user.full_name
    text = (
        f"👤 *Your Info*\n\n"
        f"Name: {tag}\n"
        f"ID: `{user.id}`\n"
        f"Status: {status}\n\n"
        f"💬 *{msg.chat.title or 'Group'}*\n"
        f"Group ID: `{chat_id}`\n"
        f"{count_line}\n"
        f"⚠️ *Warns*\n"
        f"Warnings: {warn_count}/{warn_limit}\n"
        f"Last reason: {last_reason or 'none'}"
    )

    try:
        await ctx.bot.send_message(user.id, text, parse_mode="Markdown")
        if msg.chat.type != "private":
            await msg.reply_text("📬 Your info has been sent to your DMs.")
    except Forbidden:
        await msg.reply_text(text, parse_mode="Markdown")


async def cmd_dlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to log and delete it.")
        return

    group = await db.get_group(config.DB_PATH, chat_id)
    log_channel_id = group.get("log_channel_id") if group else None

    target = msg.reply_to_message
    author = display_name(target.from_user) if target.from_user else "Unknown"
    author_id = target.from_user.id if target.from_user else "?"

    if log_channel_id:
        header = (
            f"📋 *Logged Message*\n"
            f"From: {author} (`{author_id}`)\n"
            f"Group: {msg.chat.title or str(chat_id)}\n"
            f"Logged by: {display_name(update.effective_user)}"
        )
        try:
            await ctx.bot.send_message(log_channel_id, header, parse_mode="Markdown")
            await ctx.bot.copy_message(log_channel_id, chat_id, target.message_id)
        except TelegramError as e:
            log.warning("dlog: failed to send to log channel: %s", e)

    try:
        await target.delete()
    except TelegramError:
        pass
    try:
        await msg.delete()
    except TelegramError:
        pass


async def cmd_cleanservice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not ctx.args or ctx.args[0].lower() not in ("on", "off"):
        await msg.reply_text(
            "Usage: `/cleanservice on` or `/cleanservice off`", parse_mode="Markdown"
        )
        return

    enabled = ctx.args[0].lower() == "on"
    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, clean_service_msgs=int(enabled))
    state = "enabled" if enabled else "disabled"
    await msg.reply_text(f"✅ Service message cleaning {state}.")


# --- halo ---

async def cmd_givehalo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("Reply to a user to give them halo.")
        return

    target = msg.reply_to_message.from_user
    await db.give_halo(config.DB_PATH, chat_id, target.id)
    await msg.reply_text(f"😇 {display_name(target)} has been given halo — exempt from antiflood, locks, and blocklists.")


async def cmd_removehalo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("Reply to a user to remove their halo.")
        return

    target = msg.reply_to_message.from_user
    await db.remove_halo(config.DB_PATH, chat_id, target.id)
    await msg.reply_text(f"✅ Halo removed from {display_name(target)}.")


# --- import / export settings ---

_EXPORTABLE_KEYS = {
    "warn_limit", "warn_action", "warn_mute_duration", "default_mute_duration",
    "rules", "clean_service_msgs",
    "welcome_text", "welcome_enabled", "goodbye_text", "goodbye_enabled",
}


async def cmd_exportsettings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    group = await db.get_group(config.DB_PATH, chat_id)
    if not group:
        await msg.reply_text("No settings found for this group.")
        return

    settings = {k: v for k, v in group.items() if k in _EXPORTABLE_KEYS and v is not None}
    data = json.dumps(settings, indent=2, ensure_ascii=False).encode()
    bio = io.BytesIO(data)
    bio.name = "settings.json"

    await msg.reply_document(bio, filename="settings.json", caption="📦 Group settings exported.")


async def cmd_importsettings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    doc = None
    if msg.document:
        doc = msg.document
    elif msg.reply_to_message and msg.reply_to_message.document:
        doc = msg.reply_to_message.document

    if not doc:
        await msg.reply_text(
            "Send a settings JSON file as a document (or reply to one) with `/importsettings`.",
            parse_mode="Markdown",
        )
        return

    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        raw = await tg_file.download_as_bytearray()
        settings = json.loads(raw)
    except (TelegramError, json.JSONDecodeError, ValueError) as e:
        await msg.reply_text(f"Failed to read file: {e}")
        return

    updates = {k: v for k, v in settings.items() if k in _EXPORTABLE_KEYS}
    if not updates:
        await msg.reply_text("No valid settings found in file.")
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, **updates)
    await msg.reply_text(f"✅ Imported {len(updates)} setting(s).")


# --- inactives ---

async def cmd_inactives(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    days = 7
    action = None
    if ctx.args:
        try:
            days = int(ctx.args[0])
        except ValueError:
            await msg.reply_text("Usage: `/inactives [days] [kick|ban]`", parse_mode="Markdown")
            return
        if len(ctx.args) > 1 and ctx.args[1].lower() in ("kick", "ban"):
            action = ctx.args[1].lower()

    since_ts = int(time.time()) - days * 86400
    rows = await db.get_inactives(config.DB_PATH, chat_id, since_ts)

    if not rows:
        await msg.reply_text(
            f"No tracked users inactive for {days}+ days.\n"
            "_Note: only users who have sent a message while the bot was active are tracked._",
            parse_mode="Markdown",
        )
        return

    lines = [f"💤 *Users inactive for {days}+ days* ({len(rows)} found)\n"]
    for r in rows:
        name = f"@{r['username']}" if r['username'] else r['full_name']
        last = datetime.fromtimestamp(r['last_seen'], tz=timezone.utc).strftime("%Y-%m-%d")
        lines.append(f"• {name} (`{r['user_id']}`) — last seen {last}")

    text = "\n".join(lines)

    # Send to admin DMs (list can be long)
    try:
        for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
            await ctx.bot.send_message(update.effective_user.id, chunk, parse_mode="Markdown")
        await msg.reply_text(f"📬 Inactive users list ({len(rows)}) sent to your DMs.")
    except Forbidden:
        for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
            await msg.reply_text(chunk, parse_mode="Markdown")

    if not action:
        return

    # Bulk action — cap at 50 to avoid accidents
    targets = rows[:50]
    done, failed = 0, 0
    for r in targets:
        try:
            await ctx.bot.ban_chat_member(chat_id, r["user_id"])
            if action == "kick":
                await ctx.bot.unban_chat_member(chat_id, r["user_id"])
            done += 1
        except TelegramError:
            failed += 1

    verb = "kicked" if action == "kick" else "banned"
    await msg.reply_text(
        f"✅ {verb.capitalize()} {done} inactive user(s)." +
        (f" ({failed} failed — likely already gone or admin.)" if failed else "")
    )
