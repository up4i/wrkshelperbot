import re
from telegram import User
from telegram.error import TelegramError

_DURATION_RE = re.compile(r'^(\d+)(m|h|d)$', re.IGNORECASE)
_MULTIPLIERS = {'m': 60, 'h': 3600, 'd': 86400}

def parse_duration(s: str) -> int | None:
    """Parse '30m', '2h', '7d' into seconds. Returns None if not a duration."""
    m = _DURATION_RE.match(s.strip())
    if not m:
        return None
    return int(m.group(1)) * _MULTIPLIERS[m.group(2).lower()]

def format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration string."""
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"

def display_name(user: User) -> str:
    """Return @username if available, else full_name."""
    return f"@{user.username}" if user.username else user.full_name

async def is_admin(bot, chat_id: int, user_id: int) -> bool:
    """Return True if user is a group admin or creator."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False
