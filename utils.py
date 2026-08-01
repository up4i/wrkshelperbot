import re
from decimal import Decimal, InvalidOperation
from telegram import User

_AMOUNT_RE = re.compile(r'^([+-]?\d+(?:\.\d+)?)\s*([kmb])?$', re.IGNORECASE)
_AMOUNT_MULTIPLIERS = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000}

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
