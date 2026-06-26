import time
from datetime import datetime, timezone
from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes
from telegram.error import TelegramError

import config
import db
from utils import parse_duration, format_duration, display_name, is_admin
from action_logger import log_action

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
_FREE_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
)


async def _resolve_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Returns (user_id, display_name, args_without_target) or (None, None, args).
    Pulls from reply if available, else from first @mention in args.
    """
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        user = msg.reply_to_message.from_user
        return user.id, display_name(user), ctx.args
    if ctx.args:
        first = ctx.args[0]
        if first.startswith("@"):
            return None, first, ctx.args[1:]
    return None, None, ctx.args


async def _parse_action_args(args: list[str]) -> tuple[int | None, str | None]:
    """Returns (duration_secs, reason) from args after target resolution."""
    if not args:
        return None, None
    duration = parse_duration(args[0])
    if duration is not None:
        reason = " ".join(args[1:]) or None
    else:
        duration = None
        reason = " ".join(args) or None
    return duration, reason


async def _do_mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE, delete_trigger: bool):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)

    target_id, target_name, remaining = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to mute that user.")
        return

    if await is_admin(ctx.bot, chat_id, target_id):
        await msg.reply_text("Can't mute an admin.")
        return

    duration, reason = await _parse_action_args(remaining)
    if duration is None:
        duration = group.get("default_mute_duration")

    until_date = (datetime.fromtimestamp(time.time() + duration, tz=timezone.utc) if duration else None)

    try:
        await ctx.bot.restrict_chat_member(chat_id, target_id, _MUTED_PERMS, until_date=until_date)
    except TelegramError as e:
        await msg.reply_text(f"Failed to mute: {e}")
        return

    if duration:
        await db.add_punishment(config.DB_PATH, chat_id, target_id, "mute", int(time.time()) + duration)
    else:
        await db.add_punishment(config.DB_PATH, chat_id, target_id, "mute", None)

    dur_str = f" for {format_duration(duration)}" if duration else " permanently"
    await msg.reply_text(f"🔇 {target_name} muted{dur_str}.")

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="mute", target_id=target_id, target_name=target_name,
        admin_name=display_name(update.effective_user),
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
        reason=reason, duration_secs=duration,
    )

    if delete_trigger and msg.reply_to_message:
        try:
            await msg.reply_to_message.delete()
        except TelegramError:
            pass


async def cmd_mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_mute(update, ctx, delete_trigger=False)

async def cmd_dmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_mute(update, ctx, delete_trigger=True)


async def cmd_unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)

    target_id, target_name, _ = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to unmute that user.")
        return

    try:
        await ctx.bot.restrict_chat_member(chat_id, target_id, _FREE_PERMS)
    except TelegramError as e:
        await msg.reply_text(f"Failed to unmute: {e}")
        return

    await db.remove_punishment(config.DB_PATH, chat_id, target_id, "mute")
    await msg.reply_text(f"🔊 {target_name} unmuted.")

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="unmute", target_id=target_id, target_name=target_name,
        admin_name=display_name(update.effective_user),
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
    )


async def _do_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE, delete_trigger: bool):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)

    target_id, target_name, remaining = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to ban that user.")
        return

    if await is_admin(ctx.bot, chat_id, target_id):
        await msg.reply_text("Can't ban an admin.")
        return

    duration, reason = await _parse_action_args(remaining)
    until_date = (datetime.fromtimestamp(time.time() + duration, tz=timezone.utc) if duration else None)

    try:
        await ctx.bot.ban_chat_member(chat_id, target_id, until_date=until_date)
    except TelegramError as e:
        await msg.reply_text(f"Failed to ban: {e}")
        return

    if duration:
        await db.add_punishment(config.DB_PATH, chat_id, target_id, "ban", int(time.time()) + duration)

    dur_str = f" for {format_duration(duration)}" if duration else " permanently"
    await msg.reply_text(f"🔨 {target_name} banned{dur_str}.")

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="ban", target_id=target_id, target_name=target_name,
        admin_name=display_name(update.effective_user),
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
        reason=reason, duration_secs=duration,
    )

    if delete_trigger and msg.reply_to_message:
        try:
            await msg.reply_to_message.delete()
        except TelegramError:
            pass


async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_ban(update, ctx, delete_trigger=False)

async def cmd_dban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_ban(update, ctx, delete_trigger=True)


async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)

    target_id, target_name, _ = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to unban that user.")
        return

    try:
        await ctx.bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
    except TelegramError as e:
        await msg.reply_text(f"Failed to unban: {e}")
        return

    await db.remove_punishment(config.DB_PATH, chat_id, target_id, "ban")
    await msg.reply_text(f"✅ {target_name} unbanned.")

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="unban", target_id=target_id, target_name=target_name,
        admin_name=display_name(update.effective_user),
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
    )


async def _do_kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE, delete_trigger: bool):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)

    target_id, target_name, remaining = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to kick that user.")
        return

    if await is_admin(ctx.bot, chat_id, target_id):
        await msg.reply_text("Can't kick an admin.")
        return

    reason = " ".join(remaining) or None

    try:
        await ctx.bot.ban_chat_member(chat_id, target_id)
        await ctx.bot.unban_chat_member(chat_id, target_id)
    except TelegramError as e:
        await msg.reply_text(f"Failed to kick: {e}")
        return

    await msg.reply_text(f"👢 {target_name} kicked.")

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="kick", target_id=target_id, target_name=target_name,
        admin_name=display_name(update.effective_user),
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
        reason=reason,
    )

    if delete_trigger and msg.reply_to_message:
        try:
            await msg.reply_to_message.delete()
        except TelegramError:
            pass


async def cmd_kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_kick(update, ctx, delete_trigger=False)

async def cmd_dkick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_kick(update, ctx, delete_trigger=True)


async def _do_warn(update: Update, ctx: ContextTypes.DEFAULT_TYPE, delete_trigger: bool):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)

    target_id, target_name, remaining = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to warn that user.")
        return

    if await is_admin(ctx.bot, chat_id, target_id):
        await msg.reply_text("Can't warn an admin.")
        return

    reason = " ".join(remaining) or None
    warn_limit = group.get("warn_limit", 3)
    warn_action = group.get("warn_action", "mute")

    count = await db.add_warning(config.DB_PATH, chat_id, target_id, reason or "")
    await msg.reply_text(f"⚠️ {target_name} warned. ({count}/{warn_limit})" +
                         (f"\nReason: {reason}" if reason else ""))

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="warn", target_id=target_id, target_name=target_name,
        admin_name=display_name(update.effective_user),
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
        reason=reason, warn_count=count, warn_limit=warn_limit,
    )

    if count >= warn_limit:
        await db.reset_warnings(config.DB_PATH, chat_id, target_id)
        if warn_action == "ban":
            try:
                await ctx.bot.ban_chat_member(chat_id, target_id)
            except TelegramError:
                pass
            await log_action(
                ctx.bot, group.get("log_channel_id"),
                action="ban", target_id=target_id, target_name=target_name,
                admin_name="Auto (warn limit)",
                group_id=chat_id, group_name=msg.chat.title or str(chat_id),
                reason=f"Reached warn limit ({warn_limit})",
            )
        else:
            dur = group.get("warn_mute_duration", 3600)
            until = datetime.fromtimestamp(time.time() + dur, tz=timezone.utc)
            try:
                await ctx.bot.restrict_chat_member(chat_id, target_id, _MUTED_PERMS, until_date=until)
                await db.add_punishment(config.DB_PATH, chat_id, target_id, "mute", int(time.time()) + dur)
            except TelegramError:
                pass
            await log_action(
                ctx.bot, group.get("log_channel_id"),
                action="mute", target_id=target_id, target_name=target_name,
                admin_name="Auto (warn limit)",
                group_id=chat_id, group_name=msg.chat.title or str(chat_id),
                reason=f"Reached warn limit ({warn_limit})", duration_secs=dur,
            )

    if delete_trigger and msg.reply_to_message:
        try:
            await msg.reply_to_message.delete()
        except TelegramError:
            pass


async def cmd_warn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_warn(update, ctx, delete_trigger=False)

async def cmd_dwarn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_warn(update, ctx, delete_trigger=True)


async def cmd_warns(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    target_id, target_name, _ = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to check that user's warns.")
        return

    row = await db.get_warnings(config.DB_PATH, chat_id, target_id)
    group = await db.get_group(config.DB_PATH, chat_id)
    limit = group.get("warn_limit", 3) if group else 3

    if not row or row["count"] == 0:
        await msg.reply_text(f"{target_name} has no warnings.")
    else:
        await msg.reply_text(
            f"⚠️ {target_name}: {row['count']}/{limit} warns\n"
            f"Last reason: {row['last_reason'] or 'none'}"
        )


async def cmd_resetwarns(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    group = await db.get_group(config.DB_PATH, chat_id)

    target_id, target_name, _ = await _resolve_target(update, ctx)
    if not target_id:
        await msg.reply_text("Reply to a message to reset that user's warns.")
        return

    await db.reset_warnings(config.DB_PATH, chat_id, target_id)
    await msg.reply_text(f"🔄 {target_name}'s warnings have been reset.")

    await log_action(
        ctx.bot, group.get("log_channel_id"),
        action="resetwarns", target_id=target_id, target_name=target_name,
        admin_name=display_name(update.effective_user),
        group_id=chat_id, group_name=msg.chat.title or str(chat_id),
    )
