"""
Seed the Admin's Plush Pepe gift model into gift_models.
Run once on the Pi after deploying the is_admin_gift migration.

Usage:
    python3 scripts/seed_admin_plush_pepe.py
"""
import sqlite3
import os

DB_PATH = os.path.expanduser("~/.local/share/wrkshelperbot/data.db")

COLLECTION = "Admin's Plush Pepe"
MODEL_NAME = "Admin's Plush Pepe"
CUSTOM_EMOJI_ID = "5093574893702744528"
MODEL_EMOJI = "🐸"
TIER = "legendary"
MODEL_NUMBER = 1
RARITY_PCT = 0.0  # not droppable from cases

def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    existing = con.execute(
        "SELECT id FROM gift_models WHERE collection = ?", (COLLECTION,)
    ).fetchone()

    if existing:
        print(f"Model already exists (id={existing['id']}), updating custom_emoji_id.")
        con.execute(
            "UPDATE gift_models SET custom_emoji_id = ? WHERE collection = ?",
            (CUSTOM_EMOJI_ID, COLLECTION)
        )
    else:
        con.execute(
            "INSERT INTO gift_models "
            "(collection, model_number, model_name, model_emoji, model_rarity_pct, tier, custom_emoji_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (COLLECTION, MODEL_NUMBER, MODEL_NAME, MODEL_EMOJI, RARITY_PCT, TIER, CUSTOM_EMOJI_ID)
        )
        model_id = con.execute(
            "SELECT id FROM gift_models WHERE collection = ?", (COLLECTION,)
        ).fetchone()["id"]
        print(f"Created gift_models entry id={model_id}")

    con.commit()
    con.close()
    print("Done. Now call POST /api/admin/grant-admin-gift with the target user_id.")
    print(f'  curl -X POST http://localhost:8420/api/admin/grant-admin-gift \\')
    print(f'    -H "Content-Type: application/json" \\')
    print(f"    -d '{{\"user_id\": <TELEGRAM_USER_ID>, \"collection\": \"{COLLECTION}\"}}'")

if __name__ == "__main__":
    main()
