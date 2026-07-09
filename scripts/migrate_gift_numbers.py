#!/usr/bin/env python3
"""One-time migration: add gift_number column and assign per-collection sequential numbers."""
import sqlite3
import sys
import os

DB = os.environ.get("DB_PATH", os.path.expanduser("~/wrkshelperbot/wrkshelperbot.db"))
if len(sys.argv) > 1:
    DB = sys.argv[1]

con = sqlite3.connect(DB)
cur = con.cursor()

# Add column if missing
cols = [r[1] for r in cur.execute("PRAGMA table_info(gift_instances)")]
if "gift_number" not in cols:
    cur.execute("ALTER TABLE gift_instances ADD COLUMN gift_number INTEGER")
    print("Added gift_number column.")
else:
    print("gift_number column already exists.")

# Assign sequential numbers per collection, ordered by model_number then background tier
cur.execute("""
    UPDATE gift_instances SET gift_number = (
        SELECT rn FROM (
            SELECT gi2.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY gm2.collection
                       ORDER BY gm2.model_number,
                                CASE gi2.background
                                    WHEN 'black' THEN 1 WHEN 'onyx' THEN 2 WHEN 'grape' THEN 3
                                    WHEN 'emerald' THEN 4 WHEN 'midnight' THEN 5 WHEN 'orange' THEN 6
                                    ELSE 99 END
                   ) AS rn
            FROM gift_instances gi2
            JOIN gift_models gm2 ON gm2.id = gi2.model_id
        ) ranked WHERE ranked.id = gift_instances.id
    )
    WHERE gift_number IS NULL
""")

updated = con.total_changes
con.commit()
con.close()
print(f"Assigned gift_number to {updated} instances.")
print("Done.")
