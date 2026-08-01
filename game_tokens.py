from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from market_config import GAME_TOKEN_SYMBOLS


TOKEN_DECIMALS = 9
TOKEN_SCALE = 10 ** TOKEN_DECIMALS
CUSTOM_ADDRESS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,18}[a-z0-9])?$")

GAME_TOKEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_wallets (
    user_id        INTEGER PRIMARY KEY,
    wallet_address TEXT NOT NULL UNIQUE COLLATE NOCASE,
    custom_address TEXT UNIQUE COLLATE NOCASE,
    created_at     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS game_token_balances (
    user_id INTEGER NOT NULL,
    symbol  TEXT NOT NULL,
    amount  INTEGER NOT NULL DEFAULT 0 CHECK(amount >= 0),
    PRIMARY KEY (user_id, symbol)
);
CREATE TABLE IF NOT EXISTS game_token_transactions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    transaction_type     TEXT NOT NULL,
    from_symbol          TEXT,
    to_symbol            TEXT,
    input_amount         INTEGER NOT NULL DEFAULT 0,
    output_amount        INTEGER NOT NULL DEFAULT 0,
    wrk_amount           INTEGER NOT NULL DEFAULT 0,
    counterparty_user_id INTEGER,
    price_snapshot       TEXT,
    created_at           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_game_token_transactions_user
ON game_token_transactions(user_id, created_at DESC);
"""


def default_wallet_address(user_id: int) -> str:
    digest = hashlib.blake2s(f"wrk-game-wallet:{int(user_id)}".encode(), digest_size=10).hexdigest()
    return f"wrk1{digest}"


def normalize_custom_address(value: str) -> str:
    name = value.strip().lower()
    if name.endswith(".wrk"):
        name = name[:-4]
    if not 3 <= len(name) <= 20 or not CUSTOM_ADDRESS_RE.fullmatch(name):
        raise ValueError("Use 3–20 lowercase letters, numbers, or interior hyphens")
    return f"{name}.wrk"


def parse_token_amount(value: str | int | float | Decimal) -> int:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid token amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Token amount must be positive")
    atoms = int((amount * TOKEN_SCALE).to_integral_value(rounding=ROUND_DOWN))
    if atoms <= 0:
        raise ValueError(f"Minimum token amount is 0.{'0' * (TOKEN_DECIMALS - 1)}1")
    if atoms > 9_000_000_000_000_000_000:
        raise ValueError("Token amount is too large")
    return atoms


def format_token_amount(atoms: int) -> str:
    amount = Decimal(int(atoms)) / TOKEN_SCALE
    return format(amount.normalize(), "f")
