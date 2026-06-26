import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
OWNER_ID: int = int(os.environ["OWNER_ID"])
DB_PATH: str = os.getenv("DB_PATH", os.path.expanduser("~/.local/share/wrkshelperbot/data.db"))
LOG_FILE: str = os.getenv("LOG_FILE", os.path.expanduser("~/.local/share/wrkshelperbot/bot.log"))
