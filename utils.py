import re
from decimal import Decimal, InvalidOperation
from telegram import User
from telegram.error import TelegramError

_DURATION_RE = re.compile(r'^(\d+)(m|h|d)$', re.IGNORECASE)
_MULTIPLIERS = {'m': 60, 'h': 3600, 'd': 86400}
_AMOUNT_RE = re.compile(r'^([+-]?\d+(?:\.\d+)?)\s*([kmb])?$', re.IGNORECASE)
_AMOUNT_MULTIPLIERS = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000}

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

def parse_amount(value: str, *, allow_negative: bool = False) -> int | None:
    """Parse 500, 2.5k, 10m, or 1b into a whole-number amount."""
    match = _AMOUNT_RE.fullmatch(value.replace(",", "").strip())
    if not match:
        return None
    try:
        number = Decimal(match.group(1))
        amount = int(number * _AMOUNT_MULTIPLIERS.get((match.group(2) or "").lower(), 1))
    except (InvalidOperation, OverflowError):
        return None
    if abs(amount) > 9_223_372_036_854_775_807:
        return None
    if amount < 0 and not allow_negative:
        return None
    return amount

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
