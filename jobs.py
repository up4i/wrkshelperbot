import logging
from telegram import ChatPermissions
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db

log = logging.getLogger(__name__)

_FREE_PERMS = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
)


async def sweep_punishments(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    expired = await db.get_expired_punishments(config.DB_PATH)
    for p in expired:
        chat_id, user_id, action, pid = p["chat_id"], p["user_id"], p["action"], p["id"]
        try:
            if action == "mute":
                await ctx.bot.restrict_chat_member(chat_id, user_id, _FREE_PERMS)
                log.info("Auto-unmuted %s in %s", user_id, chat_id)
            elif action == "ban":
                await ctx.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
                log.info("Auto-unbanned %s in %s", user_id, chat_id)
        except TelegramError as e:
            log.warning("Sweep error for %s/%s: %s", chat_id, user_id, e)
        finally:
            await db.delete_punishment_by_id(config.DB_PATH, pid)
