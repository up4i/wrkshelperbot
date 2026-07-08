import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from telegram import Update, ChatPermissions
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from action_logger import log_action
from utils import display_name, format_duration, is_admin

_MUTED_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
)

# In-memory rate tracking (resets on bot restart — intentional)
_flood_state: dict[tuple[int, int], deque] = defaultdict(deque)
_raid_state: dict[int, deque] = defaultdict(deque)

_LOCK_CHECKS = {
    "links":     lambda m: bool(m.entities and any(e.type in ("url", "text_link") for e in m.entities)),
    "forwards":  lambda m: bool(m.forward_date or getattr(m, "forward_origin", None)),
    "stickers":  lambda m: bool(m.sticker),
    "photos":    lambda m: bool(m.photo),
    "videos":    lambda m: bool(m.video),
    "audios":    lambda m: bool(m.audio or m.voice),
    "documents": lambda m: bool(m.document and not m.animation),
    "gifs":      lambda m: bool(m.animation),
    "polls":     lambda m: bool(m.poll),
}
LOCK_TYPES = list(_LOCK_CHECKS.keys())


# ── Shared helpers ─────────────────────────────────────────────────────────────

async def _skip(bot, chat_id: int, user_id: int) -> bool:
    """Return True if this user should be exempt from all protection."""
    if await is_admin(bot, chat_id, user_id):
        return True
    if await db.has_halo(config.DB_PATH, chat_id, user_id):
        return True
    return False


# ── Antiflood ──────────────────────────────────────────────────────────────────

async def _flood_check(bot, msg, group: dict, chat_id: int, user_id: int, name: str) -> bool:
    """Return True if flood action was taken."""
    limit = group.get("flood_limit", 0)
    if not limit:
        return False

    window = group.get("flood_window", 30)
    now = time.time()
    key = (chat_id, user_id)
    q = _flood_state[key]
    q.append(now)
    while q and now - q[0] > window:
        q.popleft()

    if len(q) < limit:
        return False

    _flood_state[key].clear()

    try:
        await msg.delete()
    except TelegramError:
        pass

    action = group.get("flood_action", "mute")
    duration = group.get("flood_mute_duration", 600)

    try:
        if action == "ban":
            await bot.ban_chat_member(chat_id, user_id)
        elif action == "kick":
            await bot.ban_chat_member(chat_id, user_id)
            await bot.unban_chat_member(chat_id, user_id)
        else:
            until = datetime.fromtimestamp(now + duration, tz=timezone.utc)
            await bot.restrict_chat_member(chat_id, user_id, _MUTED_PERMS, until_date=until)
            await db.add_punishment(config.DB_PATH, chat_id, user_id, "mute", int(now + duration))
    except TelegramError:
        pass

    try:
        await bot.send_message(
            chat_id,
            f"⚡ {name} {action}d for flooding.",
        )
    except TelegramError:
        pass

    await log_action(
        bot, group.get("log_channel_id"),
        action=f"antiflood ({action})", target_id=user_id, target_name=name,
        admin_name="Auto (antiflood)",
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
        duration_secs=duration if action == "mute" else None,
    )
    return True


async def cmd_setflood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not ctx.args:
        await msg.reply_text(
            "Usage: `/setflood <N> [window_secs]`\nSet N to 0 to disable.",
            parse_mode="Markdown",
        )
        return

    try:
        limit = int(ctx.args[0])
        window = int(ctx.args[1]) if len(ctx.args) > 1 else 30
    except ValueError:
        await msg.reply_text("Both arguments must be integers.")
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, flood_limit=limit, flood_window=window)
    if limit == 0:
        await msg.reply_text("✅ Antiflood disabled.")
    else:
        await msg.reply_text(f"✅ Antiflood: {limit} messages in {window}s.")


async def cmd_setfloodaction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not ctx.args or ctx.args[0].lower() not in ("mute", "kick", "ban"):
        await msg.reply_text("Usage: `/setfloodaction <mute|kick|ban>`", parse_mode="Markdown")
        return

    action = ctx.args[0].lower()
    updates: dict = {"flood_action": action}
    if len(ctx.args) > 1:
        try:
            updates["flood_mute_duration"] = int(ctx.args[1])
        except ValueError:
            pass

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, **updates)
    await msg.reply_text(f"✅ Flood action set to {action}.")


async def cmd_antiflood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    group = await db.get_group(config.DB_PATH, msg.chat.id)
    limit = group.get("flood_limit", 0) if group else 0
    if not limit:
        await msg.reply_text("⚡ Antiflood: ❌ disabled")
        return
    window = group.get("flood_window", 30)
    action = group.get("flood_action", "mute")
    dur = group.get("flood_mute_duration", 600)
    suffix = f" for {format_duration(dur)}" if action == "mute" else ""
    await msg.reply_text(
        f"⚡ *Antiflood*: ✅ enabled\n"
        f"Limit: {limit} messages / {window}s\n"
        f"Action: {action}{suffix}",
        parse_mode="Markdown",
    )


# ── Blocklist ──────────────────────────────────────────────────────────────────

async def _blocklist_check(bot, msg, group: dict, chat_id: int, user_id: int, name: str) -> bool:
    """Return True if blocklist action was taken."""
    text = msg.text or msg.caption or ""
    if not text:
        return False

    patterns = await db.get_blocklist(config.DB_PATH, chat_id)
    if not patterns:
        return False

    matched = next(
        (p for p in patterns if re.search(r'\b' + re.escape(p) + r'\b', text, re.IGNORECASE)),
        None,
    )
    if not matched:
        return False

    try:
        await msg.delete()
    except TelegramError:
        pass

    bl_action = group.get("blocklist_action", "delete")

    if bl_action == "warn":
        warn_limit = group.get("warn_limit", 3)
        count = await db.add_warning(config.DB_PATH, chat_id, user_id, f"blocked word: {matched}")
        try:
            await bot.send_message(
                chat_id, f"⚠️ {name} warned for a blocked word. ({count}/{warn_limit})"
            )
        except TelegramError:
            pass
    elif bl_action == "mute":
        dur = group.get("default_mute_duration") or 600
        until = datetime.fromtimestamp(time.time() + dur, tz=timezone.utc)
        try:
            await bot.restrict_chat_member(chat_id, user_id, _MUTED_PERMS, until_date=until)
            await db.add_punishment(config.DB_PATH, chat_id, user_id, "mute", int(time.time() + dur))
            await bot.send_message(chat_id, f"🔇 {name} muted for a blocked word.")
        except TelegramError:
            pass
    elif bl_action == "ban":
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await bot.send_message(chat_id, f"🔨 {name} banned for a blocked word.")
        except TelegramError:
            pass

    await log_action(
        bot, group.get("log_channel_id"),
        action=f"blocklist ({bl_action})", target_id=user_id, target_name=name,
        admin_name="Auto (blocklist)",
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
        reason=f"matched pattern: {matched}",
    )
    return True


async def cmd_addblocked(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return
    if not ctx.args:
        await msg.reply_text("Usage: `/addblocked <word or phrase>`", parse_mode="Markdown")
        return

    pattern = " ".join(ctx.args)
    await db.add_blocked_pattern(config.DB_PATH, chat_id, pattern)
    await msg.reply_text(f"✅ `{pattern}` added to blocklist.", parse_mode="Markdown")


async def cmd_removeblocked(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return
    if not ctx.args:
        await msg.reply_text("Usage: `/removeblocked <word or phrase>`", parse_mode="Markdown")
        return

    pattern = " ".join(ctx.args)
    removed = await db.remove_blocked_pattern(config.DB_PATH, chat_id, pattern)
    if removed:
        await msg.reply_text(f"✅ `{pattern}` removed from blocklist.", parse_mode="Markdown")
    else:
        await msg.reply_text(f"No entry found for `{pattern}`.", parse_mode="Markdown")


async def cmd_blocklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    patterns = await db.get_blocklist(config.DB_PATH, msg.chat.id)
    if not patterns:
        await msg.reply_text("Blocklist is empty.")
        return
    group = await db.get_group(config.DB_PATH, msg.chat.id)
    bl_action = group.get("blocklist_action", "delete") if group else "delete"
    lines = [f"🚫 *Blocklist* ({len(patterns)}) — action: {bl_action}\n"]
    lines += [f"• `{p}`" for p in patterns]
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_setblocklistaction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    valid = ("delete", "warn", "mute", "ban")
    if not ctx.args or ctx.args[0].lower() not in valid:
        await msg.reply_text(
            f"Usage: `/setblocklistaction <{'|'.join(valid)}>`", parse_mode="Markdown"
        )
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, blocklist_action=ctx.args[0].lower())
    await msg.reply_text(f"✅ Blocklist action: {ctx.args[0].lower()}")


# ── Locks ──────────────────────────────────────────────────────────────────────

async def _locks_check(msg, group: dict) -> bool:
    """Return True if message was deleted due to a lock."""
    locks_raw = group.get("locks")
    if not locks_raw:
        return False

    locked = set(locks_raw.split(","))
    for lock_type, check in _LOCK_CHECKS.items():
        if lock_type in locked and check(msg):
            try:
                await msg.delete()
            except TelegramError:
                pass
            return True
    return False


async def cmd_lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    valid = LOCK_TYPES + ["all"]
    if not ctx.args or ctx.args[0].lower() not in valid:
        await msg.reply_text(
            f"Usage: `/lock <type>`\nTypes: `{'` `'.join(valid)}`", parse_mode="Markdown"
        )
        return

    lock_type = ctx.args[0].lower()
    group = await db.get_group(config.DB_PATH, chat_id)
    current = set(group.get("locks", "").split(",")) if group and group.get("locks") else set()
    current.discard("")

    current = set(LOCK_TYPES) if lock_type == "all" else current | {lock_type}

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, locks=",".join(sorted(current)) or None)
    await msg.reply_text(f"🔒 `{lock_type}` locked.", parse_mode="Markdown")


async def cmd_unlock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    valid = LOCK_TYPES + ["all"]
    if not ctx.args or ctx.args[0].lower() not in valid:
        await msg.reply_text(
            f"Usage: `/unlock <type>`\nTypes: `{'` `'.join(valid)}`", parse_mode="Markdown"
        )
        return

    lock_type = ctx.args[0].lower()
    group = await db.get_group(config.DB_PATH, chat_id)
    current = set(group.get("locks", "").split(",")) if group and group.get("locks") else set()
    current.discard("")

    current = set() if lock_type == "all" else current - {lock_type}

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, locks=",".join(sorted(current)) or None)
    await msg.reply_text(f"🔓 `{lock_type}` unlocked.", parse_mode="Markdown")


async def cmd_locks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    group = await db.get_group(config.DB_PATH, msg.chat.id)
    locked = set(group.get("locks", "").split(",")) if group and group.get("locks") else set()
    locked.discard("")
    lines = ["🔒 *Lock Status*\n"]
    for lt in LOCK_TYPES:
        icon = "🔒" if lt in locked else "🔓"
        lines.append(f"{icon} `{lt}`")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Combined protection message handler ────────────────────────────────────────

async def on_protection_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.from_user or msg.from_user.is_bot:
        return

    chat_id = msg.chat.id
    user_id = msg.from_user.id

    if await _skip(ctx.bot, chat_id, user_id):
        return

    group = await db.get_group(config.DB_PATH, chat_id)
    if not group:
        return

    name = display_name(msg.from_user)

    if await _flood_check(ctx.bot, msg, group, chat_id, user_id, name):
        return
    if await _blocklist_check(ctx.bot, msg, group, chat_id, user_id, name):
        return
    await _locks_check(msg, group)


# ── Antiraid ───────────────────────────────────────────────────────────────────

async def on_antiraid_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.new_chat_members:
        return

    chat_id = msg.chat.id
    group = await db.get_group(config.DB_PATH, chat_id)
    if not group or not group.get("antiraid_enabled", 0):
        return

    limit = group.get("antiraid_limit", 5)
    window = group.get("antiraid_window", 30)
    mute_dur = group.get("antiraid_mute_duration", 600)
    now = time.time()

    q = _raid_state[chat_id]
    for user in msg.new_chat_members:
        if not user.is_bot:
            q.append(now)
    while q and now - q[0] > window:
        q.popleft()

    if len(q) < limit:
        return

    until = datetime.fromtimestamp(now + mute_dur, tz=timezone.utc)
    muted = 0
    for user in msg.new_chat_members:
        if user.is_bot:
            continue
        try:
            await ctx.bot.restrict_chat_member(chat_id, user.id, _MUTED_PERMS, until_date=until)
            muted += 1
        except TelegramError:
            pass

    if muted:
        try:
            await ctx.bot.send_message(
                chat_id,
                f"🚨 *Raid detected!* {len(q)} joins in {window}s — "
                f"new members muted for {format_duration(mute_dur)}.",
                parse_mode="Markdown",
            )
        except TelegramError:
            pass

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="antiraid", target_id=0, target_name=f"{len(q)} users",
        admin_name="Auto (antiraid)",
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
        reason=f"{len(q)} joins in {window}s",
    )


async def cmd_antiraid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return
    if not ctx.args or ctx.args[0].lower() not in ("on", "off"):
        await msg.reply_text("Usage: `/antiraid on|off`", parse_mode="Markdown")
        return

    enabled = ctx.args[0].lower() == "on"
    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, antiraid_enabled=int(enabled))
    await msg.reply_text(f"✅ Antiraid {'enabled' if enabled else 'disabled'}.")


async def cmd_setantiraid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return
    if len(ctx.args or []) < 2:
        await msg.reply_text(
            "Usage: `/setantiraid <joins> <window_secs> [mute_secs]`", parse_mode="Markdown"
        )
        return

    try:
        limit = int(ctx.args[0])
        window = int(ctx.args[1])
        mute_dur = int(ctx.args[2]) if len(ctx.args) > 2 else 600
    except ValueError:
        await msg.reply_text("All arguments must be integers.")
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(
        config.DB_PATH, chat_id,
        antiraid_limit=limit, antiraid_window=window, antiraid_mute_duration=mute_dur,
    )
    await msg.reply_text(
        f"✅ Antiraid: {limit} joins / {window}s → mute {format_duration(mute_dur)}."
    )
