#!/usr/bin/env python3
"""
Apply model_emoji_ids.json to the gift_models table in the database.
Updates custom_emoji_id for each (collection, model_name) pair.

Usage:
    cd /home/ogkush/Projects/wrkshelperbot
    python3 scripts/apply_emoji_ids.py
"""

import asyncio
import json
import sys
import os

import re
import unicodedata

import aiosqlite
from dotenv import load_dotenv


def clean_model_name(name: str) -> str:
    """Strip leading emoji / unicode remnants from parsed model names."""
    # Remove zero-width joiners, variation selectors, black squares that leak from emoji
    name = re.sub(r'^[‍‌️⃣⬛\s]+', '', name)
    # Strip any remaining leading non-letter chars
    name = re.sub(r'^[^\w\'"(]+', '', name)
    return name.strip()

load_dotenv()

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/.local/share/wrkshelperbot/data.db"))
IDS_PATH = os.path.join(os.path.dirname(__file__), "../data/model_emoji_ids.json")


async def main():
    with open(IDS_PATH) as f:
        id_map = json.load(f)

    updated = 0
    not_found = []

    async with aiosqlite.connect(DB_PATH) as db:
        for collection, model_map in id_map.items():
            for raw_name, emoji_id in model_map.items():
                model_name = clean_model_name(raw_name)
                if not model_name or len(model_name) > 80:
                    continue
                async with db.execute(
                    "SELECT id FROM gift_models WHERE collection=? AND model_name=?",
                    (collection, model_name)
                ) as cur:
                    row = await cur.fetchone()

                if row:
                    await db.execute(
                        "UPDATE gift_models SET custom_emoji_id=? WHERE collection=? AND model_name=?",
                        (str(emoji_id), collection, model_name)
                    )
                    updated += 1
                else:
                    not_found.append(f"{collection}/{model_name}")

        await db.commit()

    print(f"Updated {updated} models with custom_emoji_id")
    if not_found:
        print(f"\nNot found in DB ({len(not_found)}):")
        for x in not_found[:20]:
            print(f"  {x}")
        if len(not_found) > 20:
            print(f"  ... and {len(not_found) - 20} more")

    # Verify
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM gift_models WHERE custom_emoji_id IS NOT NULL") as cur:
            row = await cur.fetchone()
        print(f"\nTotal models with custom_emoji_id in DB: {row[0]}")


if __name__ == "__main__":
    asyncio.run(main())
