import logging
import random
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


async def daily_price_update(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs at midnight: apply random drift + demand pressure, then reset pressure."""
    prices = await db.get_all_gift_prices(config.DB_PATH)

    for p in prices:
        base = p["base_price"]
        current = p["current_price"]
        floor_price = int(base * 0.40)
        ceil_price = int(base * 5.0)

        drift_pct = random.uniform(-0.20, 0.20)

        demand = p["demand_pressure"]
        if demand > 0:
            demand_pct = min(demand * 0.03, 0.30)
        elif demand < 0:
            demand_pct = max(demand * 0.02, -0.30)
        else:
            demand_pct = 0.0

        new_price = int(current * (1 + drift_pct + demand_pct))
        new_price = max(floor_price, min(ceil_price, new_price))

        await db.update_gift_price(config.DB_PATH, p["collection"], p["background"], new_price)

    await db.reset_demand_pressure(config.DB_PATH)
    log.info("daily_price_update: updated %d price rows", len(prices))
