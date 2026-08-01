import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set. Copy .env.example to .env and fill in your bot token.")

OWNER_ID: int = 0
_owner_str = os.environ.get("OWNER_ID", "")
if not _owner_str:
    raise ValueError("OWNER_ID not set. Copy .env.example to .env and fill in your Telegram user ID.")
try:
    OWNER_ID = int(_owner_str)
except ValueError:
    raise ValueError(f"OWNER_ID must be an integer, got: {_owner_str!r}")

DB_PATH: str = os.getenv("DB_PATH", os.path.expanduser("~/.local/share/wrkshelperbot/data.db"))
LOG_FILE: str = os.getenv("LOG_FILE", os.path.expanduser("~/.local/share/wrkshelperbot/bot.log"))
MINI_APP_URL: str = os.getenv("MINI_APP_URL", "https://t.me/wrkshelperbot/app")
WRK_PER_USD: int = int(os.getenv("WRK_PER_USD", "1000000"))
CUSTOM_ADDRESS_GRAM_PRICE: str = os.getenv("CUSTOM_ADDRESS_GRAM_PRICE", "5")
if WRK_PER_USD <= 0:
    raise ValueError("WRK_PER_USD must be positive")
