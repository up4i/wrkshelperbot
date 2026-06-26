import logging
import os
from logging.handlers import RotatingFileHandler

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

import config
from db import init_db
from jobs import sweep_punishments
from handlers.moderation import (
    cmd_mute, cmd_dmute, cmd_unmute,
    cmd_ban, cmd_dban, cmd_unban,
    cmd_kick, cmd_dkick,
    cmd_warn, cmd_dwarn, cmd_warns, cmd_resetwarns,
)
from handlers.setup import cmd_setup, setup_callback
from handlers.admin import cmd_id, cmd_info, cmd_setlog, on_any_message

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
    app.add_handler(CommandHandler("setup",      cmd_setup))
    app.add_handler(CommandHandler("id",         cmd_id))
    app.add_handler(CommandHandler("info",       cmd_info))
    app.add_handler(CommandHandler("setlog",     cmd_setlog))
    app.add_handler(CallbackQueryHandler(setup_callback, pattern=r"^setup:"))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.ALL, on_any_message))

    app.job_queue.run_repeating(sweep_punishments, interval=60, first=10)

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
