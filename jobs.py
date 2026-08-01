import asyncio
import logging
import random
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from simulated_market import simulate_market_activity

log = logging.getLogger(__name__)


async def simulated_market_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate light fictional tape activity between user trades."""
    try:
        count = await asyncio.to_thread(simulate_market_activity, config.DB_PATH)
        if count:
            log.debug("simulated_market_tick: executed %d market trades", count)
    except Exception as exc:
        log.warning("simulated_market_tick failed: %s", exc)

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


async def sweep_work_reminders(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    import time
    now = int(time.time())
    targets = await db.get_work_reminder_targets(config.DB_PATH, now)
    for user_id in targets:
        try:
            await ctx.bot.send_message(
                user_id,
                "⚡ Your shift is ready! Use /work to start earning.",
            )
            await db.mark_reminder_sent(config.DB_PATH, user_id, now)
        except TelegramError as e:
            log.warning("work_reminder send failed for %s: %s", user_id, e)
