import logging
import os
import traceback
from logging.handlers import RotatingFileHandler

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

import config
from command_catalog import COMMANDS, PUBLIC_COMMANDS
from db import init_db
import datetime
from jobs import daily_price_update, simulated_market_tick, sweep_work_reminders
from handlers.core import (
    cmd_app,
    cmd_coins,
    cmd_help,
    cmd_start,
    cmd_wallet,
    on_bot_added,
    on_group_activity,
)
from handlers.economy import (
    cmd_balance, cmd_daily, cmd_leaderboard,
    cmd_rob, cmd_slots, cmd_coinflip, cmd_dice,
    cmd_blackjack, blackjack_callback,
    cmd_crash, cmd_cashout,
    cmd_give, cmd_givewrk, cmd_setwrk, cmd_giveadminpepe,
    cmd_addecoadmin, cmd_removeecoadmin, cmd_listecoadmins,
    cmd_hack, cmd_guess,
    cmd_work, cmd_workreminder, cmd_jobs, work_callback,
    cmd_profile, cmd_flex, lb_callback,
)
from handlers.gifts import (
    cmd_seedgifts,
    cmd_inventory, cmd_gift,
    cmd_shop, cmd_buy, cmd_sell,
    cmd_offer, cmd_offers,
    cmd_pin, pin_callback,
    gifts_callback, gift_offer_callback, shop_callback,
)

os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        RotatingFileHandler(config.LOG_FILE, maxBytes=5_000_000, backupCount=3),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

_PUBLIC_COMMANDS = list(PUBLIC_COMMANDS)


_COMMAND_HANDLERS = {
    "cmd_start": cmd_start,
    "cmd_app": cmd_app,
    "cmd_help": cmd_help,
    "cmd_wallet": cmd_wallet,
    "cmd_coins": cmd_coins,
    "cmd_balance": cmd_balance,
    "cmd_daily": cmd_daily,
    "cmd_leaderboard": cmd_leaderboard,
    "cmd_profile": cmd_profile,
    "cmd_flex": cmd_flex,
    "cmd_rob": cmd_rob,
    "cmd_slots": cmd_slots,
    "cmd_coinflip": cmd_coinflip,
    "cmd_dice": cmd_dice,
    "cmd_blackjack": cmd_blackjack,
    "cmd_crash": cmd_crash,
    "cmd_cashout": cmd_cashout,
    "cmd_hack": cmd_hack,
    "cmd_guess": cmd_guess,
    "cmd_work": cmd_work,
    "cmd_workreminder": cmd_workreminder,
    "cmd_jobs": cmd_jobs,
    "cmd_give": cmd_give,
    "cmd_inventory": cmd_inventory,
    "cmd_gift": cmd_gift,
    "cmd_pin": cmd_pin,
    "cmd_shop": cmd_shop,
    "cmd_buy": cmd_buy,
    "cmd_sell": cmd_sell,
    "cmd_offer": cmd_offer,
    "cmd_offers": cmd_offers,
}


def build_app() -> Application:
    app = Application.builder().token(config.BOT_TOKEN).build()

    for command in COMMANDS:
        kwargs = {"filters": filters.ChatType.PRIVATE} if command.private_only else {}
        app.add_handler(CommandHandler(list(command.names), _COMMAND_HANDLERS[command.handler], **kwargs))

    app.add_handler(CommandHandler("givewrk",        cmd_givewrk))
    app.add_handler(CommandHandler("setwrk",         cmd_setwrk))
    app.add_handler(CommandHandler("giveadminpepe",    cmd_giveadminpepe))
    app.add_handler(CommandHandler("addecoadmin",      cmd_addecoadmin))
    app.add_handler(CommandHandler("removeecoadmin",   cmd_removeecoadmin))
    app.add_handler(CommandHandler("listecoadmins",    cmd_listecoadmins))
    app.add_handler(CommandHandler("seedgifts",  cmd_seedgifts))
    app.add_handler(CallbackQueryHandler(lb_callback,      pattern=r"^lb:"))
    app.add_handler(CallbackQueryHandler(pin_callback,     pattern=r"^pin:"))
    app.add_handler(CallbackQueryHandler(blackjack_callback, pattern=r"^bj:"))
    app.add_handler(CallbackQueryHandler(work_callback, pattern=r"^work:(tap|end):"))
    app.add_handler(CallbackQueryHandler(gifts_callback,      pattern=r"^gifts:"))
    app.add_handler(CallbackQueryHandler(gift_offer_callback, pattern=r"^gift_offer:"))
    app.add_handler(CallbackQueryHandler(shop_callback,       pattern=r"^shop:"))
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL,
            on_group_activity,
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS, on_bot_added),
        group=2,
    )

    app.job_queue.run_repeating(sweep_work_reminders, interval=60, first=30)
    app.job_queue.run_repeating(simulated_market_tick, interval=60, first=15)
    app.job_queue.run_daily(daily_price_update, time=datetime.time(hour=0, minute=0))

    app.add_error_handler(error_handler)
    return app


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    tb = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    log.error("Unhandled exception:\n%s", tb)
    try:
        await context.bot.send_message(
            chat_id=config.OWNER_ID,
            text=f"⚠️ Bot error:\n<code>{tb[-3000:]}</code>",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def post_init(app: Application) -> None:
    await init_db(config.DB_PATH)
    try:
        await app.bot.set_my_commands([
            BotCommand(command, description)
            for command, description in _PUBLIC_COMMANDS
        ])
    except Exception as exc:
        log.warning("Could not publish Telegram command menu: %s", exc)
    log.info("DB initialized at %s", config.DB_PATH)


def main():
    app = build_app()
    app.post_init = post_init
    log.info("wrkshelperbot starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
