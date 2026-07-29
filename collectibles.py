"""Shared pricing and formatting rules for WRK$ anonymous numbers."""

ANON_PREFIX = "+888"
ANON_FLOOR_PRICE = 30_000_000
ANON_MIN_SUFFIX = 1
ANON_MAX_SUFFIX = 999
ANON_FIREWALL_COOLDOWN = 24 * 60 * 60
ANON_VAULT_WITHDRAW_DELAY = 24 * 60 * 60

_SEQUENCES = {
    "012", "123", "234", "345", "456", "567", "678", "789",
    "987", "876", "765", "654", "543", "432", "321", "210",
}


def format_anon_number(suffix: int) -> str:
    """Return the six-digit display form, for example ``+888 001``."""
    if not ANON_MIN_SUFFIX <= suffix <= ANON_MAX_SUFFIX:
        raise ValueError("anonymous number suffix must be between 001 and 999")
    return f"{ANON_PREFIX} {suffix:03d}"


def anon_number_rarity(suffix: int) -> tuple[str, float]:
    """Return a display rarity and deterministic floor multiplier."""
    if not ANON_MIN_SUFFIX <= suffix <= ANON_MAX_SUFFIX:
        raise ValueError("anonymous number suffix must be between 001 and 999")

    digits = f"{suffix:03d}"
    if len(set(digits)) == 1:
        return "iconic", 4.0
    if suffix <= 9:
        return "iconic", 3.0
    if digits in _SEQUENCES:
        return "elite", 2.5
    if digits.endswith("00"):
        return "elite", 2.25
    if digits[0] == digits[2]:
        return "premium", 1.75
    if digits[0] == digits[1] or digits[1] == digits[2]:
        return "select", 1.35
    return "standard", 1.0


def anon_number_price(suffix: int) -> int:
    """Return the seeded primary-market price for an anonymous number."""
    _, multiplier = anon_number_rarity(suffix)
    return int(ANON_FLOOR_PRICE * multiplier)
