import logging
import os
from logging.handlers import RotatingFileHandler

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

import config
from db import init_db
import datetime
from jobs import sweep_punishments, daily_price_update
from handlers.moderation import (
    cmd_mute, cmd_dmute, cmd_unmute,
    cmd_ban, cmd_dban, cmd_unban,
    cmd_kick, cmd_dkick,
    cmd_warn, cmd_dwarn, cmd_warns, cmd_resetwarns,
    cmd_report, cmd_purge, cmd_promote, cmd_demote,
)
from handlers.setup import cmd_setup, setup_callback, cmd_connect, cmd_start, dsetup_callback
from handlers.autoreply import cmd_addautoreply, cmd_removeautoreply, cmd_autoreplies, on_message_autoreply
from handlers.protection import (
    cmd_setflood, cmd_setfloodaction, cmd_antiflood,
    cmd_addblocked, cmd_removeblocked, cmd_blocklist, cmd_setblocklistaction,
    cmd_lock, cmd_unlock, cmd_locks,
    cmd_antiraid, cmd_setantiraid,
    on_protection_check, on_antiraid_check,
)
from handlers.admin import cmd_id, cmd_info, cmd_setlog, cmd_halos, cmd_help, cmd_econhelp, cmd_setbottopic, cmd_clearbottopic, help_callback, on_any_message, on_service_message
from handlers.utility import (
    cmd_admins, cmd_rules, cmd_setrules, cmd_me, cmd_dlog, cmd_cleanservice,
    cmd_givehalo, cmd_removehalo,
    cmd_exportsettings, cmd_importsettings,
    cmd_inactives,
)
from handlers.welcome import (
    cmd_setwelcome, cmd_setgoodbye, cmd_welcome, cmd_goodbye,
    on_new_member, on_member_left,
)
from handlers.economy import (
    cmd_balance, cmd_daily, cmd_leaderboard,
    cmd_rob, cmd_slots, cmd_coinflip, cmd_dice,
    cmd_blackjack, blackjack_callback,
    cmd_crash, cmd_cashout,
    cmd_give, cmd_givewrk, cmd_setwrk,
    cmd_hack, cmd_guess,
    cmd_work, cmd_jobs, work_callback,
)
from handlers.gifts import (
    cmd_seedgifts,
    cmd_inventory, cmd_gift,
    cmd_shop, cmd_buy, cmd_sell,
    cmd_offer, cmd_offers,
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


def build_app() -> Application:
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("mute",       cmd_mute))
    app.add_handler(CommandHandler("dmute",      cmd_dmute))
    app.add_handler(CommandHandler("unmute",     cmd_unmute))
    app.add_handler(CommandHandler("ban",        cmd_ban))
    app.add_handler(CommandHandler("dban",       cmd_dban))
    app.add_handler(CommandHandler("unban",      cmd_unban))
    app.add_handler(CommandHandler("kick",       cmd_kick))
    app.add_handler(CommandHandler("dkick",      cmd_dkick))
    app.add_handler(CommandHandler("warn",       cmd_warn))
    app.add_handler(CommandHandler("dwarn",      cmd_dwarn))
    app.add_handler(CommandHandler("warns",      cmd_warns))
    app.add_handler(CommandHandler("resetwarns", cmd_resetwarns))
    app.add_handler(CommandHandler("report",     cmd_report))
    app.add_handler(CommandHandler("purge",      cmd_purge))
    app.add_handler(CommandHandler("promote",    cmd_promote))
    app.add_handler(CommandHandler("demote",     cmd_demote))
    app.add_handler(CommandHandler("setup",        cmd_setup))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("econhelp",     cmd_econhelp))
    app.add_handler(CommandHandler("id",           cmd_id))
    app.add_handler(CommandHandler("info",         cmd_info))
    app.add_handler(CommandHandler("setlog",       cmd_setlog))
    app.add_handler(CommandHandler("setbottopic",  cmd_setbottopic))
    app.add_handler(CommandHandler("clearbottopic", cmd_clearbottopic))
    app.add_handler(CommandHandler("admins",       cmd_admins))
    app.add_handler(CommandHandler("rules",        cmd_rules))
    app.add_handler(CommandHandler("setrules",     cmd_setrules))
    app.add_handler(CommandHandler("me",           cmd_me))
    app.add_handler(CommandHandler("dlog",           cmd_dlog))
    app.add_handler(CommandHandler("cleanservice",   cmd_cleanservice))
    app.add_handler(CommandHandler("setwelcome",     cmd_setwelcome))
    app.add_handler(CommandHandler("setgoodbye",     cmd_setgoodbye))
    app.add_handler(CommandHandler("welcome",        cmd_welcome))
    app.add_handler(CommandHandler("goodbye",        cmd_goodbye))
    app.add_handler(CommandHandler("givehalo",       cmd_givehalo))
    app.add_handler(CommandHandler("removehalo",     cmd_removehalo))
    app.add_handler(CommandHandler("halos",          cmd_halos))
    app.add_handler(CommandHandler("exportsettings", cmd_exportsettings))
    app.add_handler(CommandHandler("importsettings", cmd_importsettings))
    app.add_handler(CommandHandler("inactives",        cmd_inactives))
    app.add_handler(CommandHandler("connect",          cmd_connect))
    app.add_handler(CommandHandler("start",            cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("addautoreply",       cmd_addautoreply))
    app.add_handler(CommandHandler("removeautoreply",    cmd_removeautoreply))
    app.add_handler(CommandHandler("autoreplies",        cmd_autoreplies))
    app.add_handler(CommandHandler("setflood",           cmd_setflood))
    app.add_handler(CommandHandler("setfloodaction",     cmd_setfloodaction))
    app.add_handler(CommandHandler("antiflood",          cmd_antiflood))
    app.add_handler(CommandHandler("addblocked",         cmd_addblocked))
    app.add_handler(CommandHandler("removeblocked",      cmd_removeblocked))
    app.add_handler(CommandHandler("blocklist",          cmd_blocklist))
    app.add_handler(CommandHandler("setblocklistaction", cmd_setblocklistaction))
    app.add_handler(CommandHandler("lock",               cmd_lock))
    app.add_handler(CommandHandler("unlock",             cmd_unlock))
    app.add_handler(CommandHandler("locks",              cmd_locks))
    app.add_handler(CommandHandler("antiraid",           cmd_antiraid))
    app.add_handler(CommandHandler("setantiraid",        cmd_setantiraid))
    app.add_handler(CommandHandler("balance",     cmd_balance))
    app.add_handler(CommandHandler("bal",         cmd_balance))
    app.add_handler(CommandHandler("b",           cmd_balance))
    app.add_handler(CommandHandler("daily",       cmd_daily))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("lb",          cmd_leaderboard))
    app.add_handler(CommandHandler("rob",         cmd_rob))
    app.add_handler(CommandHandler("slots",       cmd_slots))
    app.add_handler(CommandHandler("coinflip",    cmd_coinflip))
    app.add_handler(CommandHandler("cf",          cmd_coinflip))
    app.add_handler(CommandHandler("dice",        cmd_dice))
    app.add_handler(CommandHandler("blackjack",   cmd_blackjack))
    app.add_handler(CommandHandler("bj",          cmd_blackjack))
    app.add_handler(CommandHandler("crash",       cmd_crash))
    app.add_handler(CommandHandler("cashout",     cmd_cashout))
    app.add_handler(CommandHandler("hack",        cmd_hack))
    app.add_handler(CommandHandler("guess",       cmd_guess))
    app.add_handler(CommandHandler("work",        cmd_work))
    app.add_handler(CommandHandler("jobs",        cmd_jobs))
    app.add_handler(CommandHandler("give",        cmd_give))
    app.add_handler(CommandHandler("givewrk",     cmd_givewrk))
    app.add_handler(CommandHandler("setwrk",      cmd_setwrk))
    app.add_handler(CommandHandler("seedgifts",  cmd_seedgifts))
    app.add_handler(CommandHandler("inventory",  cmd_inventory))
    app.add_handler(CommandHandler("inv",        cmd_inventory))
    app.add_handler(CommandHandler("gift",       cmd_gift))
    app.add_handler(CommandHandler("shop",       cmd_shop))
    app.add_handler(CommandHandler("buy",        cmd_buy))
    app.add_handler(CommandHandler("sell",       cmd_sell))
    app.add_handler(CommandHandler("offer",      cmd_offer))
    app.add_handler(CommandHandler("offers",     cmd_offers))
    app.add_handler(CallbackQueryHandler(setup_callback,  pattern=r"^setup:"))
    app.add_handler(CallbackQueryHandler(dsetup_callback, pattern=r"^dsetup:"))
    app.add_handler(CallbackQueryHandler(help_callback,   pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(blackjack_callback, pattern=r"^bj:"))
    app.add_handler(CallbackQueryHandler(work_callback, pattern=r"^work:pick:"))
    app.add_handler(CallbackQueryHandler(gifts_callback,      pattern=r"^gifts:"))
    app.add_handler(CallbackQueryHandler(gift_offer_callback, pattern=r"^gift_offer:"))
    app.add_handler(CallbackQueryHandler(shop_callback,       pattern=r"^shop:"))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.ALL, on_any_message))
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.StatusUpdate.ALL, on_service_message),
        group=1,
    )
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member),
        group=2,
    )
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.StatusUpdate.LEFT_CHAT_MEMBER, on_member_left),
        group=2,
    )
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_message_autoreply),
        group=3,
    )
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND & ~filters.StatusUpdate.ALL, on_protection_check),
        group=4,
    )
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS, on_antiraid_check),
        group=5,
    )

    app.job_queue.run_repeating(sweep_punishments, interval=60, first=10)
    app.job_queue.run_daily(daily_price_update, time=datetime.time(hour=0, minute=0))

    return app


async def post_init(app: Application) -> None:
    await init_db(config.DB_PATH)
    log.info("DB initialized at %s", config.DB_PATH)


def main():
    app = build_app()
    app.post_init = post_init
    log.info("wrkshelperbot starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
