#!/usr/bin/env python3
"""
Reprice all gift collections based on tier.

Tier price ranges (base = orange/1x background):
  high: 1,500,000 – 2,800,000  → black (3x) = 4.5M – 8.4M
  mid:     70,000 –   300,000  → black (3x) = 210k – 900k
  low:      7,000 –    33,000  → black (3x) =  21k – 100k

Uses a hash of the collection name for deterministic variation within each range.

Usage:
    cd ~/wrkshelperbot && python3 scripts/reprice_gifts.py
"""

import asyncio
import hashlib
import os
import sys
import time

import aiosqlite
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.gift_catalog import CATALOG

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/.local/share/wrkshelperbot/data.db"))

_BG_MULTIPLIERS = {
    "black": 3.0, "onyx": 2.5, "grape": 2.0,
    "emerald": 1.5, "midnight": 1.2, "orange": 1.0,
}

TIER_RANGES = {
    "high": (6_000_000, 12_000_000),
    "mid":    (150_000,    600_000),
    "low":     (10_000,     40_000),
}

# Explicit overrides for culturally significant collections (base/orange price).
# Background multipliers (black=3x, onyx=2.5x, etc.) still apply on top.
PRICE_OVERRIDES = {
    "plush_pepe":     10_000_000,
    "scared_cat":      9_000_000,
    "durovs_cap":     12_000_000,
    "astral_shard":    9_500_000,
    "rare_bird":       9_000_000,
    "westside_sign":   8_500_000,
    "precious_peach":  8_000_000,
    "kissed_frog":     7_500_000,
    "artisan_brick":   6_000_000,
    "mighty_arm":      7_000_000,
    "heart_locket":    8_000_000,
    "swiss_watch":       500_000,
    "crystal_ball":      400_000,
}


def _base_price(collection_key: str, tier: str) -> int:
    if collection_key in PRICE_OVERRIDES:
        return PRICE_OVERRIDES[collection_key]
    lo, hi = TIER_RANGES[tier]
    h = int(hashlib.md5(collection_key.encode()).hexdigest(), 16)
    frac = (h % 10_000) / 10_000
    return int(lo + frac * (hi - lo))


async def main():
    now = int(time.time())
    updated = 0

    async with aiosqlite.connect(DB_PATH) as db:
        for col_key, col in CATALOG.items():
            tier = col["tier"]
            base = _base_price(col_key, tier)
            for bg, mult in _BG_MULTIPLIERS.items():
                price = int(base * mult)
                await db.execute(
                    """UPDATE gift_prices
                       SET base_price=?, current_price=?, last_updated=?
                       WHERE collection=? AND background=?""",
                    (base, price, now, col_key, bg)
                )
                updated += 1
        await db.commit()

    print(f"Updated {updated} price rows\n")
    print("Sample prices:")
    for tier in ("high", "mid", "low"):
        examples = [(k, v) for k, v in CATALOG.items() if v["tier"] == tier][:2]
        for col_key, col in examples:
            base = _base_price(col_key, tier)
            black = int(base * 3.0)
            orange = base
            print(f"  [{tier}] {col_key}: orange={orange:,} WRK$  black={black:,} WRK$")


if __name__ == "__main__":
    asyncio.run(main())
