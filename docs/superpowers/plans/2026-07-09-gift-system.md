# Gift System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 1/1 collectible gift system on top of the WRK$ economy — catalog seeding, inventory display, bank buy/sell, P2P offers, daily drop integration, and a daily price engine.

**Architecture:** Static gift catalog in `data/gift_catalog.py` (parsed from gifts.txt) feeds a seed function that populates four new SQLite tables. All gift commands live in `handlers/gifts.py`. The daily price job runs in `jobs.py` at midnight. Catalog data and game logic are fully separated — the catalog is a plain Python dict, never touched at runtime.

**Tech Stack:** python-telegram-bot 21+, aiosqlite, APScheduler (PTB job_queue), pytest-asyncio

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `scripts/parse_gifts.py` | Create | One-time script: parses gifts.txt → data/gift_catalog.py |
| `data/__init__.py` | Create | Empty, makes data/ a package |
| `data/gift_catalog.py` | Create (generated) | Static catalog dict with all collections, models, tiers, base prices |
| `db.py` | Modify | 4 new tables in _SCHEMA + all gift DB functions |
| `handlers/gifts.py` | Create | All gift commands + offer callback handler |
| `jobs.py` | Modify | Add daily_price_update job |
| `bot.py` | Modify | Import + register gift handlers and price job |
| `tests/test_gift_db.py` | Create | Tests for all gift DB functions |
| `tests/test_gift_logic.py` | Create | Tests for pure gift helpers |

---

## Task 1: Parse gifts.txt → data/gift_catalog.py

**Files:**
- Create: `scripts/parse_gifts.py`
- Create: `data/__init__.py`
- Create: `data/gift_catalog.py` (generated output)

- [ ] **Step 1: Create the scripts directory and parser**

```bash
mkdir -p /home/ogkush/Projects/wrkshelperbot/scripts
mkdir -p /home/ogkush/Projects/wrkshelperbot/data
touch /home/ogkush/Projects/wrkshelperbot/data/__init__.py
```

Create `/home/ogkush/Projects/wrkshelperbot/scripts/parse_gifts.py`:

```python
#!/usr/bin/env python3
"""Parse /home/ogkush/Desktop/gifts.txt into data/gift_catalog.py."""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Tier and base_price for each collection (key = snake_case collection name).
# Low-tier collections not listed here default to ("low", 1000).
TIER_MAP = {
    # ── High tier ──────────────────────────────────────────────────────────
    "artisan_brick":    ("high", 45000),
    "astral_shard":     ("high", 50000),
    "durovs_cap":       ("high", 130000),
    "heart_locket":     ("high", 120000),
    "heroic_helmet":    ("high", 40000),
    "kissed_frog":      ("high", 25000),
    "mighty_arm":       ("high", 55000),
    "nail_bracelet":    ("high", 60000),
    "neko_helmet":      ("high", 22000),
    "plush_pepe":       ("high", 140000),
    "precious_peach":   ("high", 70000),
    "rare_bird":        ("high", 65000),
    "scared_cat":       ("high", 125000),
    "westside_sign":    ("high", 28000),
    # ── Mid tier ───────────────────────────────────────────────────────────
    "bonded_ring":      ("mid", 7000),
    "crystal_ball":     ("mid", 6500),
    "cupid_charm":      ("mid", 6000),
    "diamond_ring":     ("mid", 7500),
    "electric_skull":   ("mid", 6000),
    "eternal_rose":     ("mid", 5500),
    "gem_signet":       ("mid", 3500),
    "ion_gem":          ("mid", 5000),
    "ionic_dryer":      ("mid", 10000),
    "khabibs_papakha":  ("mid", 6500),
    "loot_bag":         ("mid", 5500),
    "love_potion":      ("mid", 5000),
    "low_rider":        ("mid", 7000),
    "mad_pumpkin":      ("mid", 5500),
    "magic_potion":     ("mid", 3000),
    "mini_oscar":       ("mid", 6000),
    "perfume_bottle":   ("mid", 7000),
    "record_player":    ("mid", 6500),
    "sharp_tongue":     ("mid", 5000),
    "signet_ring":      ("mid", 6000),
    "snoop_cigar":      ("mid", 4000),
    "swiss_watch":      ("mid", 11000),
    "top_hat":          ("mid", 3500),
    "trapped_heart":    ("mid", 3500),
    "ufc_strike":       ("mid", 7000),
    "vintage_cigar":    ("mid", 10000),
    "voodoo_doll":      ("mid", 4000),
}

def to_key(name: str) -> str:
    name = name.replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

def parse_gifts(path: str) -> dict:
    collections: dict = {}
    current: str | None = None
    model_num = 0

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()

            # Collection header: "Gift Models 🎁: <Name> Models <emoji>"
            m = re.search(r"Gift Models 🎁: (.+?) Models", line)
            if m:
                name = m.group(1).strip()
                if "Crafted" in name:
                    current = None
                    continue
                key = to_key(name)
                current = key
                model_num = 0
                collections[key] = {"name": name, "emoji": "🎁", "models": []}
                continue

            if current is None:
                continue

            # Model line: <emoji> <name> — <pct>%
            m = re.match(r"^(\S+)\s+(.+?)\s+—\s+([\d.]+)%\s*$", line)
            if not m:
                continue
            emoji, model_name, pct = m.group(1), m.group(2).strip(), float(m.group(3))
            if not collections[current]["models"]:
                collections[current]["emoji"] = emoji
            model_num += 1
            collections[current]["models"].append({
                "number": model_num,
                "name": model_name,
                "rarity_pct": pct,
                "custom_emoji_id": None,
            })

    return collections

def generate(path: str) -> str:
    collections = parse_gifts(path)
    lines = ["CATALOG = {"]
    for key in sorted(collections):
        col = collections[key]
        if not col["models"]:
            continue
        tier, base_price = TIER_MAP.get(key, ("low", 1000))
        lines.append(f'    "{key}": {{')
        lines.append(f'        "name": {col["name"]!r},')
        lines.append(f'        "emoji": {col["emoji"]!r},')
        lines.append(f'        "tier": {tier!r},')
        lines.append(f'        "base_price": {base_price},')
        lines.append(f'        "models": [')
        for mdl in col["models"]:
            lines.append(
                f'            {{"number": {mdl["number"]}, "name": {mdl["name"]!r}, '
                f'"rarity_pct": {mdl["rarity_pct"]}, "custom_emoji_id": None}},'
            )
        lines.append(f'        ],')
        lines.append(f'    }},')
    lines.append("}")
    return "\n".join(lines)

if __name__ == "__main__":
    src = "/home/ogkush/Desktop/gifts.txt"
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "gift_catalog.py")
    catalog = generate(src)
    with open(out, "w", encoding="utf-8") as f:
        f.write(catalog + "\n")
    print(f"Written to {out}")
    from data.gift_catalog import CATALOG
    total_models = sum(len(c["models"]) for c in CATALOG.values())
    print(f"Collections: {len(CATALOG)}, Total models: {total_models}, Total instances: {total_models * 6}")
```

- [ ] **Step 2: Run the parser**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python scripts/parse_gifts.py
```

Expected output (approximate):
```
Written to /home/ogkush/Projects/wrkshelperbot/data/gift_catalog.py
Collections: 109, Total models: XXXX, Total instances: XXXX
```

Verify the generated file starts with `CATALOG = {` and spot-check a few collections.

- [ ] **Step 3: Verify catalog imports cleanly**

```bash
python -c "from data.gift_catalog import CATALOG; print(f'{len(CATALOG)} collections loaded')"
```

Expected: `109 collections loaded` (or close to it — crafted collections are excluded)

- [ ] **Step 4: Commit**

```bash
git add scripts/parse_gifts.py data/__init__.py data/gift_catalog.py
git commit -m "feat: gift catalog generated from gifts.txt (no crafted models)"
```

---

## Task 2: Gift DB Tables and Functions

**Files:**
- Modify: `db.py`
- Create: `tests/test_gift_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gift_db.py`:

```python
import time
import pytest
from db import (
    init_db,
    seed_gifts, is_gifts_seeded,
    get_user_gifts, get_gift_instance, get_gift_instance_by_spec,
    transfer_gift,
    get_bank_gifts,
    get_gift_price, update_gift_price, apply_demand_pressure,
    create_offer, get_offers_for_user, get_offer, update_offer_status,
    expire_old_offers,
)

MINI_CATALOG = {
    "test_gift": {
        "name": "Test Gift",
        "emoji": "🎁",
        "tier": "low",
        "base_price": 1000,
        "models": [
            {"number": 1, "name": "Alpha", "rarity_pct": 1.0, "custom_emoji_id": None},
            {"number": 2, "name": "Beta",  "rarity_pct": 2.0, "custom_emoji_id": None},
        ],
    }
}

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_seed_gifts_creates_instances(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    # 2 models × 6 backgrounds = 12 instances
    gifts = await get_bank_gifts(db_path)
    assert len(gifts) == 12


@pytest.mark.asyncio
async def test_seed_gifts_idempotent(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    await seed_gifts(db_path, MINI_CATALOG)
    gifts = await get_bank_gifts(db_path)
    assert len(gifts) == 12


@pytest.mark.asyncio
async def test_is_gifts_seeded(db_path):
    await init_db(db_path)
    assert await is_gifts_seeded(db_path) is False
    await seed_gifts(db_path, MINI_CATALOG)
    assert await is_gifts_seeded(db_path) is True


@pytest.mark.asyncio
async def test_transfer_gift_changes_owner(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    bank_gifts = await get_bank_gifts(db_path)
    instance_id = bank_gifts[0]["id"]
    await transfer_gift(db_path, instance_id, 999)
    instance = await get_gift_instance(db_path, instance_id)
    assert instance["owner_id"] == 999


@pytest.mark.asyncio
async def test_get_user_gifts(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    bank_gifts = await get_bank_gifts(db_path)
    await transfer_gift(db_path, bank_gifts[0]["id"], 999)
    await transfer_gift(db_path, bank_gifts[1]["id"], 999)
    user_gifts = await get_user_gifts(db_path, 999)
    assert len(user_gifts) == 2


@pytest.mark.asyncio
async def test_get_gift_instance_by_spec(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    instance = await get_gift_instance_by_spec(db_path, "test_gift", 1, "black")
    assert instance is not None
    assert instance["model_name"] == "Alpha"
    assert instance["background"] == "black"


@pytest.mark.asyncio
async def test_gift_price_operations(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    price = await get_gift_price(db_path, "test_gift", "orange")
    assert price["current_price"] == 1000  # base_price * 1.0 orange multiplier
    await update_gift_price(db_path, "test_gift", "orange", 1200)
    price = await get_gift_price(db_path, "test_gift", "orange")
    assert price["current_price"] == 1200


@pytest.mark.asyncio
async def test_demand_pressure(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    await apply_demand_pressure(db_path, "test_gift", "orange", +1)
    price = await get_gift_price(db_path, "test_gift", "orange")
    assert price["demand_pressure"] == 1


@pytest.mark.asyncio
async def test_offer_lifecycle(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    bank = await get_bank_gifts(db_path)
    await transfer_gift(db_path, bank[0]["id"], 111)
    offer_id = await create_offer(db_path, from_user_id=222, to_user_id=111, instance_id=bank[0]["id"], wrk_offered=500)
    offer = await get_offer(db_path, offer_id)
    assert offer["status"] == "pending"
    await update_offer_status(db_path, offer_id, "accepted")
    offer = await get_offer(db_path, offer_id)
    assert offer["status"] == "accepted"


@pytest.mark.asyncio
async def test_expire_old_offers(db_path):
    await init_db(db_path)
    await seed_gifts(db_path, MINI_CATALOG)
    bank = await get_bank_gifts(db_path)
    await transfer_gift(db_path, bank[0]["id"], 111)
    # Create offer with past created_at
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO gift_offers (from_user_id, to_user_id, instance_id, wrk_offered, status, created_at) VALUES (?,?,?,?,?,?)",
            (222, 111, bank[0]["id"], 500, "pending", int(time.time()) - 90000)
        )
        await db.commit()
    expired = await expire_old_offers(db_path)
    assert len(expired) == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -m pytest tests/test_gift_db.py -v 2>&1 | head -15
```

Expected: ImportError (DB functions don't exist yet)

- [ ] **Step 3: Add 4 gift tables to `_SCHEMA` in `db.py`**

Add after the `economy` table block (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS gift_models (
    id               INTEGER PRIMARY KEY,
    collection       TEXT NOT NULL,
    model_number     INTEGER NOT NULL,
    model_name       TEXT NOT NULL,
    model_emoji      TEXT NOT NULL,
    model_rarity_pct REAL NOT NULL,
    tier             TEXT NOT NULL,
    custom_emoji_id  TEXT,
    UNIQUE(collection, model_number)
);
CREATE TABLE IF NOT EXISTS gift_instances (
    id          INTEGER PRIMARY KEY,
    model_id    INTEGER NOT NULL REFERENCES gift_models(id),
    background  TEXT NOT NULL,
    owner_id    INTEGER,
    acquired_at INTEGER,
    UNIQUE(model_id, background)
);
CREATE TABLE IF NOT EXISTS gift_prices (
    collection      TEXT NOT NULL,
    background      TEXT NOT NULL,
    base_price      INTEGER NOT NULL,
    current_price   INTEGER NOT NULL,
    demand_pressure INTEGER NOT NULL DEFAULT 0,
    last_updated    INTEGER NOT NULL,
    PRIMARY KEY (collection, background)
);
CREATE TABLE IF NOT EXISTS gift_offers (
    id           INTEGER PRIMARY KEY,
    from_user_id INTEGER NOT NULL,
    to_user_id   INTEGER NOT NULL,
    instance_id  INTEGER NOT NULL REFERENCES gift_instances(id),
    wrk_offered  INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   INTEGER NOT NULL
);
```

- [ ] **Step 4: Add gift DB functions to `db.py`**

Append after the `claim_daily` function:

```python
# --- gifts ---

_BG_MULTIPLIERS = {
    "black": 3.0, "onyx": 2.5, "grape": 2.0,
    "emerald": 1.5, "midnight": 1.2, "orange": 1.0,
}
_BACKGROUNDS = ["black", "onyx", "grape", "emerald", "midnight", "orange"]


async def seed_gifts(db_path: str, catalog: dict) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        for col_key, col in catalog.items():
            for mdl in col["models"]:
                await db.execute(
                    """INSERT OR IGNORE INTO gift_models
                       (collection, model_number, model_name, model_emoji, model_rarity_pct, tier, custom_emoji_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (col_key, mdl["number"], mdl["name"], col["emoji"],
                     mdl["rarity_pct"], col["tier"], mdl.get("custom_emoji_id")),
                )
                await db.commit()
                async with db.execute(
                    "SELECT id FROM gift_models WHERE collection=? AND model_number=?",
                    (col_key, mdl["number"])
                ) as cur:
                    row = await cur.fetchone()
                    model_id = row[0]
                for bg in _BACKGROUNDS:
                    await db.execute(
                        "INSERT OR IGNORE INTO gift_instances (model_id, background) VALUES (?, ?)",
                        (model_id, bg)
                    )
            for bg in _BACKGROUNDS:
                price = int(col["base_price"] * _BG_MULTIPLIERS[bg])
                await db.execute(
                    """INSERT OR IGNORE INTO gift_prices
                       (collection, background, base_price, current_price, demand_pressure, last_updated)
                       VALUES (?, ?, ?, ?, 0, ?)""",
                    (col_key, bg, col["base_price"], price, now)
                )
        await db.commit()


async def is_gifts_seeded(db_path: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM gift_models") as cur:
            row = await cur.fetchone()
            return row[0] > 0


async def get_user_gifts(db_path: str, user_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id, gi.background, gi.acquired_at,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.model_rarity_pct, gm.tier
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.owner_id = ?
               ORDER BY gm.collection, gm.model_number, gi.background""",
            (user_id,)
        ) as cur:
            return [dict(r) async for r in cur]


async def get_gift_instance(db_path: str, instance_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id, gi.background, gi.owner_id, gi.acquired_at,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.model_rarity_pct, gm.tier
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.id = ?""",
            (instance_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_gift_instance_by_spec(db_path: str, collection: str, model_number: int, background: str) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id, gi.background, gi.owner_id, gi.acquired_at,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.model_rarity_pct, gm.tier
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gm.collection = ? AND gm.model_number = ? AND gi.background = ?""",
            (collection, model_number, background)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def transfer_gift(db_path: str, instance_id: int, new_owner_id: int | None) -> None:
    now = int(time.time()) if new_owner_id else None
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE gift_instances SET owner_id = ?, acquired_at = ? WHERE id = ?",
            (new_owner_id, now, instance_id)
        )
        await db.commit()


async def get_bank_gifts(db_path: str, collection: str | None = None) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if collection:
            sql = """SELECT gi.id, gi.background,
                            gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                            gm.model_rarity_pct, gm.tier
                     FROM gift_instances gi
                     JOIN gift_models gm ON gm.id = gi.model_id
                     WHERE gi.owner_id IS NULL AND gm.collection = ?
                     ORDER BY gm.model_number, gi.background"""
            params = (collection,)
        else:
            sql = """SELECT gi.id, gi.background,
                            gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                            gm.model_rarity_pct, gm.tier
                     FROM gift_instances gi
                     JOIN gift_models gm ON gm.id = gi.model_id
                     WHERE gi.owner_id IS NULL
                     ORDER BY gm.collection, gm.model_number, gi.background"""
            params = ()
        async with db.execute(sql, params) as cur:
            return [dict(r) async for r in cur]


async def get_gift_price(db_path: str, collection: str, background: str) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM gift_prices WHERE collection=? AND background=?",
            (collection, background)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_gift_prices(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM gift_prices") as cur:
            return [dict(r) async for r in cur]


async def update_gift_price(db_path: str, collection: str, background: str, new_price: int) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE gift_prices SET current_price=?, last_updated=? WHERE collection=? AND background=?",
            (new_price, now, collection, background)
        )
        await db.commit()


async def apply_demand_pressure(db_path: str, collection: str, background: str, delta: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE gift_prices SET demand_pressure = demand_pressure + ? WHERE collection=? AND background=?",
            (delta, collection, background)
        )
        await db.commit()


async def reset_demand_pressure(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE gift_prices SET demand_pressure = 0")
        await db.commit()


async def create_offer(db_path: str, from_user_id: int, to_user_id: int, instance_id: int, wrk_offered: int) -> int:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "INSERT INTO gift_offers (from_user_id, to_user_id, instance_id, wrk_offered, status, created_at) VALUES (?,?,?,?,?,?)",
            (from_user_id, to_user_id, instance_id, wrk_offered, "pending", now)
        ) as cur:
            await db.commit()
            return cur.lastrowid


async def get_offer(db_path: str, offer_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM gift_offers WHERE id=?", (offer_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_offers_for_user(db_path: str, user_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT go.*, gi.background,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji
               FROM gift_offers go
               JOIN gift_instances gi ON gi.id = go.instance_id
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE (go.from_user_id=? OR go.to_user_id=?) AND go.status='pending'
               ORDER BY go.created_at DESC""",
            (user_id, user_id)
        ) as cur:
            return [dict(r) async for r in cur]


async def update_offer_status(db_path: str, offer_id: int, status: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE gift_offers SET status=? WHERE id=?", (status, offer_id))
        await db.commit()


async def expire_old_offers(db_path: str) -> list[int]:
    cutoff = int(time.time()) - 86400  # 24 hours
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id FROM gift_offers WHERE status='pending' AND created_at < ?", (cutoff,)
        ) as cur:
            rows = [r[0] async for r in cur]
        if rows:
            await db.execute(
                f"UPDATE gift_offers SET status='expired' WHERE id IN ({','.join('?' for _ in rows)})",
                rows
            )
            await db.commit()
        return rows


async def get_random_low_tier_bank_gift(db_path: str) -> dict | None:
    """Returns a random unowned low-tier instance, weighted by model rarity and bg drop weight."""
    import random
    _BG_DROP_WEIGHTS = {"black": 1, "onyx": 2, "grape": 4, "emerald": 8, "midnight": 15, "orange": 30}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id, gi.background, gm.model_rarity_pct
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.owner_id IS NULL AND gm.tier = 'low'""",
        ) as cur:
            candidates = [dict(r) async for r in cur]
    if not candidates:
        return None
    weights = [
        (1.0 / c["model_rarity_pct"]) * _BG_DROP_WEIGHTS[c["background"]]
        for c in candidates
    ]
    chosen = random.choices(candidates, weights=weights, k=1)[0]
    return await get_gift_instance(db_path, chosen["id"])
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_gift_db.py -v
```

Expected: all 10 tests PASS

- [ ] **Step 6: Run full suite to confirm no regressions**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add db.py tests/test_gift_db.py
git commit -m "feat: gift system DB tables and functions"
```

---

## Task 3: Gift Logic Tests

**Files:**
- Create: `tests/test_gift_logic.py`

These test pure helper functions that will live in `handlers/gifts.py`.

- [ ] **Step 1: Create `tests/test_gift_logic.py`**

```python
import pytest
from handlers.gifts import (
    _bg_emoji, _bg_label, _tier_label,
    _format_gift_card, _collection_display_name,
    _price_floor, _price_ceiling,
)

def test_bg_emoji():
    assert _bg_emoji("black") == "⬛"
    assert _bg_emoji("onyx") == "🖤"
    assert _bg_emoji("grape") == "🟣"
    assert _bg_emoji("emerald") == "🟢"
    assert _bg_emoji("midnight") == "🔵"
    assert _bg_emoji("orange") == "🟠"

def test_bg_label():
    assert _bg_label("black") == "Black"
    assert _bg_label("onyx") == "Onyx Black"
    assert _bg_label("midnight") == "Midnight Blue"

def test_tier_label():
    assert _tier_label("low") == "⚪ Common"
    assert _tier_label("mid") == "🔵 Rare"
    assert _tier_label("high") == "🟡 Legendary"

def test_format_gift_card():
    instance = {
        "collection": "scared_cat",
        "model_number": 12,
        "model_name": "Garfield",
        "model_emoji": "🐈‍⬛",
        "model_rarity_pct": 0.5,
        "background": "black",
        "tier": "high",
    }
    card = _format_gift_card(instance, current_price=4200)
    assert "Scared Cat" in card
    assert "#12" in card
    assert "Garfield" in card
    assert "0.5%" in card
    assert "Black" in card
    assert "4,200" in card

def test_collection_display_name():
    assert _collection_display_name("scared_cat") == "Scared Cat"
    assert _collection_display_name("jack_in_the_box") == "Jack In The Box"
    assert _collection_display_name("durovs_cap") == "Durovs Cap"

def test_price_floor_ceiling():
    assert _price_floor(1000) == 400
    assert _price_ceiling(1000) == 5000
    assert _price_floor(10000) == 4000
    assert _price_ceiling(10000) == 50000
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_gift_logic.py -v 2>&1 | head -10
```

Expected: ImportError (handlers/gifts.py doesn't exist)

- [ ] **Step 3: Commit test file**

```bash
git add tests/test_gift_logic.py
git commit -m "test: gift logic tests (failing - TDD)"
```

---

## Task 4: Create `handlers/gifts.py` — Pure Helpers + Seeding + /inv + /gift

**Files:**
- Create: `handlers/gifts.py`

- [ ] **Step 1: Create `handlers/gifts.py`**

```python
import random
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import display_name, is_admin

log = logging.getLogger(__name__)

_BG_EMOJIS = {
    "black": "⬛", "onyx": "🖤", "grape": "🟣",
    "emerald": "🟢", "midnight": "🔵", "orange": "🟠",
}
_BG_LABELS = {
    "black": "Black", "onyx": "Onyx Black", "grape": "Grape",
    "emerald": "Emerald", "midnight": "Midnight Blue", "orange": "Orange",
}
_BG_MULTIPLIERS = {
    "black": 3.0, "onyx": 2.5, "grape": 2.0,
    "emerald": 1.5, "midnight": 1.2, "orange": 1.0,
}
_BACKGROUNDS = ["black", "onyx", "grape", "emerald", "midnight", "orange"]
_GIFTS_PER_PAGE = 5


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _bg_emoji(bg: str) -> str:
    return _BG_EMOJIS.get(bg, "🎁")

def _bg_label(bg: str) -> str:
    return _BG_LABELS.get(bg, bg.title())

def _tier_label(tier: str) -> str:
    return {"low": "⚪ Common", "mid": "🔵 Rare", "high": "🟡 Legendary"}.get(tier, tier)

def _collection_display_name(key: str) -> str:
    return " ".join(w.capitalize() for w in key.split("_"))

def _price_floor(base_price: int) -> int:
    return int(base_price * 0.40)

def _price_ceiling(base_price: int) -> int:
    return int(base_price * 5.0)

def _format_gift_card(instance: dict, current_price: int) -> str:
    col_name = _collection_display_name(instance["collection"])
    num = instance["model_number"]
    bg_emoji = _bg_emoji(instance["background"])
    bg_label = _bg_label(instance["background"])
    bg_mult = _BG_MULTIPLIERS.get(instance["background"], 1.0)
    return (
        f"{instance['model_emoji']} *{col_name} #{num}*\n\n"
        f"Model: {instance['model_emoji']} {instance['model_name']} · {instance['model_rarity_pct']}%\n"
        f"Background: {bg_emoji} {bg_label} · {bg_mult}x\n"
        f"Rarity: {_tier_label(instance['tier'])}\n\n"
        f"💰 Current value: {current_price:,} WRK$"
    )


# ── /seedgifts (owner only) ───────────────────────────────────────────────────

async def cmd_seedgifts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != config.OWNER_ID:
        return

    from data.gift_catalog import CATALOG
    await msg.reply_text("⏳ Seeding gift catalog... this may take a moment.")
    await db.seed_gifts(config.DB_PATH, CATALOG)
    total = sum(len(c["models"]) * 6 for c in CATALOG.values())
    await msg.reply_text(f"✅ Gift catalog seeded.\n{len(CATALOG)} collections · {total:,} unique instances")


# ── /inv ─────────────────────────────────────────────────────────────────────

def _inv_keyboard(gifts: list[dict], page: int, user_id: int) -> InlineKeyboardMarkup:
    start = page * _GIFTS_PER_PAGE
    page_gifts = gifts[start:start + _GIFTS_PER_PAGE]
    rows = []
    for g in page_gifts:
        col_name = _collection_display_name(g["collection"])
        label = f"{g['model_emoji']} {col_name} #{g['model_number']} {_bg_emoji(g['background'])}"
        rows.append([InlineKeyboardButton(label, callback_data=f"gifts:detail:{user_id}:{g['id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"gifts:page:{user_id}:{page - 1}"))
    total_pages = (len(gifts) + _GIFTS_PER_PAGE - 1) // _GIFTS_PER_PAGE
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"gifts:page:{user_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def cmd_inventory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    gifts = await db.get_user_gifts(config.DB_PATH, user.id)
    if not gifts:
        await msg.reply_text("🎁 Your gift inventory is empty. Try `/daily` or `/shop`!", parse_mode="Markdown")
        return
    kb = _inv_keyboard(gifts, page=0, user_id=user.id)
    total_pages = (len(gifts) + _GIFTS_PER_PAGE - 1) // _GIFTS_PER_PAGE
    await msg.reply_text(
        f"🎁 *Your Gifts* ({len(gifts)} total · page 1/{total_pages})",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def gifts_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    caller_id = int(parts[2])

    if query.from_user.id != caller_id:
        await query.answer("This isn't your inventory.", show_alert=True)
        return

    if action == "page":
        page = int(parts[3])
        gifts = await db.get_user_gifts(config.DB_PATH, caller_id)
        kb = _inv_keyboard(gifts, page=page, user_id=caller_id)
        total_pages = (len(gifts) + _GIFTS_PER_PAGE - 1) // _GIFTS_PER_PAGE
        await query.edit_message_text(
            f"🎁 *Your Gifts* ({len(gifts)} total · page {page + 1}/{total_pages})",
            parse_mode="Markdown",
            reply_markup=kb
        )

    elif action == "detail":
        instance_id = int(parts[3])
        page = int(parts[4])
        instance = await db.get_gift_instance(config.DB_PATH, instance_id)
        if not instance or instance["owner_id"] != caller_id:
            await query.edit_message_text("❌ Gift not found or no longer yours.")
            return
        price_row = await db.get_gift_price(config.DB_PATH, instance["collection"], instance["background"])
        current_price = price_row["current_price"] if price_row else 0
        card = _format_gift_card(instance, current_price)
        back_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back", callback_data=f"gifts:page:{caller_id}:{page}")
        ]])
        await query.edit_message_text(card, parse_mode="Markdown", reply_markup=back_kb)


# ── /gift <collection> <number> [background] ─────────────────────────────────

async def cmd_gift(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if not ctx.args or len(ctx.args) < 2:
        await msg.reply_text("Usage: `/gift <collection> <number> [background]`\nExample: `/gift scared_cat 12 black`", parse_mode="Markdown")
        return

    collection = ctx.args[0].lower()
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[1])
    background = ctx.args[2].lower() if len(ctx.args) > 2 else None

    if background and background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    # Find user's instance(s) matching collection + model_number
    all_gifts = await db.get_user_gifts(config.DB_PATH, user.id)
    matches = [g for g in all_gifts if g["collection"] == collection and g["model_number"] == model_number]
    if not matches:
        await msg.reply_text("❌ You don't own that gift.")
        return

    if background:
        matches = [g for g in matches if g["background"] == background]
        if not matches:
            await msg.reply_text(f"❌ You don't own that gift with a {_bg_label(background)} background.")
            return

    # Default: rarest background the user owns
    bg_order = {bg: i for i, bg in enumerate(_BACKGROUNDS)}
    instance = min(matches, key=lambda g: bg_order.get(g["background"], 99))

    price_row = await db.get_gift_price(config.DB_PATH, instance["collection"], instance["background"])
    current_price = price_row["current_price"] if price_row else 0
    card = _format_gift_card(instance, current_price)
    await msg.reply_text(f"✨ {display_name(user)} is flexing:\n\n{card}", parse_mode="Markdown")
```

- [ ] **Step 2: Run logic tests**

```bash
python -m pytest tests/test_gift_logic.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add handlers/gifts.py
git commit -m "feat: gift helpers + /inv inventory UI + /gift flex command"
```

---

## Task 5: /shop, /buy, /sell (Bank Trading)

**Files:**
- Modify: `handlers/gifts.py`

- [ ] **Step 1: Append shop/buy/sell handlers to `handlers/gifts.py`**

```python
# ── /shop ─────────────────────────────────────────────────────────────────────

async def cmd_shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    collection = ctx.args[0].lower() if ctx.args else None

    if collection:
        bank_gifts = await db.get_bank_gifts(config.DB_PATH, collection)
        if not bank_gifts:
            col_name = _collection_display_name(collection)
            await msg.reply_text(f"❌ No {col_name} gifts available from the bank right now.")
            return

        lines = [f"🏪 *{_collection_display_name(collection)} — Bank Stock*\n"]
        seen = set()
        for g in bank_gifts:
            key = (g["model_number"], g["background"])
            if key in seen:
                continue
            seen.add(key)
            price_row = await db.get_gift_price(config.DB_PATH, g["collection"], g["background"])
            price = price_row["current_price"] if price_row else 0
            lines.append(
                f"{g['model_emoji']} #{g['model_number']} {g['model_name']} "
                f"{_bg_emoji(g['background'])} {_bg_label(g['background'])} "
                f"— {price:,} WRK$"
            )
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        # Show all collections the bank has stock for
        bank_gifts = await db.get_bank_gifts(config.DB_PATH)
        if not bank_gifts:
            await msg.reply_text("🏪 Bank has no gifts in stock.")
            return
        collections_in_stock = sorted({g["collection"] for g in bank_gifts})
        lines = ["🏪 *Bank Stock — Collections Available*\n"]
        lines += [f"• `{c}` — {_collection_display_name(c)}" for c in collections_in_stock]
        lines.append("\nUse `/shop <collection>` to see models and prices.")
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /buy ──────────────────────────────────────────────────────────────────────

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if len(ctx.args) < 3:
        await msg.reply_text("Usage: `/buy <collection> <number> <background>`\nExample: `/buy scared_cat 12 black`", parse_mode="Markdown")
        return

    collection = ctx.args[0].lower()
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[1])
    background = ctx.args[2].lower()

    if background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    instance = await db.get_gift_instance_by_spec(config.DB_PATH, collection, model_number, background)
    if not instance:
        await msg.reply_text("❌ Gift not found.")
        return
    if instance["owner_id"] is not None:
        await msg.reply_text("❌ That gift is already owned by someone. Use `/offer` to trade with them.")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, background)
    if not price_row:
        await msg.reply_text("❌ No price data for that gift.")
        return
    price = price_row["current_price"]

    wallet = await db.get_wallet(config.DB_PATH, user.id)
    if not wallet:
        await msg.reply_text("❌ You don't have a wallet yet. Use `/daily` to create one.")
        return
    if wallet["balance"] < price:
        await msg.reply_text(f"❌ Not enough WRK$. Price: {price:,} · Your balance: {wallet['balance']:,}")
        return

    new_bal = await db.update_balance(config.DB_PATH, user.id, -price)
    await db.transfer_gift(config.DB_PATH, instance["id"], user.id)
    await db.apply_demand_pressure(config.DB_PATH, collection, background, +1)

    col_name = _collection_display_name(collection)
    await msg.reply_text(
        f"✅ Purchased!\n\n"
        f"{instance['model_emoji']} *{col_name} #{model_number}* {_bg_emoji(background)} {_bg_label(background)}\n"
        f"Paid: {price:,} WRK$\n"
        f"💰 Balance: {new_bal:,} WRK$",
        parse_mode="Markdown"
    )


# ── /sell ─────────────────────────────────────────────────────────────────────

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if len(ctx.args) < 3:
        await msg.reply_text("Usage: `/sell <collection> <number> <background>`\nExample: `/sell scared_cat 12 black`", parse_mode="Markdown")
        return

    collection = ctx.args[0].lower()
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[1])
    background = ctx.args[2].lower()

    if background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    instance = await db.get_gift_instance_by_spec(config.DB_PATH, collection, model_number, background)
    if not instance or instance["owner_id"] != user.id:
        await msg.reply_text("❌ You don't own that gift.")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, background)
    sell_price = int(price_row["current_price"] * 0.80) if price_row else 0

    await db.transfer_gift(config.DB_PATH, instance["id"], None)  # back to bank
    new_bal = await db.update_balance(config.DB_PATH, user.id, sell_price)
    await db.apply_demand_pressure(config.DB_PATH, collection, background, -1)

    col_name = _collection_display_name(collection)
    await msg.reply_text(
        f"✅ Sold to bank!\n\n"
        f"{instance['model_emoji']} *{col_name} #{model_number}* {_bg_emoji(background)} {_bg_label(background)}\n"
        f"You received: {sell_price:,} WRK$ (80% of market)\n"
        f"💰 Balance: {new_bal:,} WRK$",
        parse_mode="Markdown"
    )
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add handlers/gifts.py
git commit -m "feat: /shop, /buy, /sell bank trading commands"
```

---

## Task 6: /offer and /offers (P2P Trading)

**Files:**
- Modify: `handlers/gifts.py`

- [ ] **Step 1: Append offer handlers to `handlers/gifts.py`**

```python
# ── /offer ────────────────────────────────────────────────────────────────────

async def cmd_offer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    # Usage: /offer @username <amount> for <collection> <number> <background>
    # e.g.:  /offer @jerry 5000 for scared_cat 12 black
    if len(ctx.args) < 6 or ctx.args[2].lower() != "for":
        await msg.reply_text(
            "Usage: `/offer @username <amount> for <collection> <number> <background>`\n"
            "Example: `/offer @jerry 5000 for scared_cat 12 black`",
            parse_mode="Markdown"
        )
        return

    target_username = ctx.args[0]
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Amount must be a number.")
        return
    wrk_amount = int(ctx.args[1])
    collection = ctx.args[3].lower()
    if not ctx.args[4].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[4])
    background = ctx.args[5].lower()

    if background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    # Find the target user
    target_row = await db.get_user_by_username(config.DB_PATH, msg.chat.id, target_username)
    if not target_row:
        await msg.reply_text("❌ Can't find that user. They need to have sent a message here first.")
        return
    target_id = target_row["user_id"]
    target_name = target_row.get("full_name") or target_username

    if target_id == user.id:
        await msg.reply_text("❌ You can't offer to yourself.")
        return

    # Check the gift exists and is owned by the target
    instance = await db.get_gift_instance_by_spec(config.DB_PATH, collection, model_number, background)
    if not instance:
        await msg.reply_text("❌ Gift not found.")
        return
    if instance["owner_id"] != target_id:
        await msg.reply_text(f"❌ {target_name} doesn't own that gift.")
        return

    # Check sender has the WRK$
    wallet = await db.get_wallet(config.DB_PATH, user.id)
    if not wallet or wallet["balance"] < wrk_amount:
        await msg.reply_text(f"❌ You don't have enough WRK$. Balance: {wallet['balance'] if wallet else 0:,}")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, background)
    market_price = price_row["current_price"] if price_row else 0

    offer_id = await db.create_offer(config.DB_PATH, user.id, target_id, instance["id"], wrk_amount)

    col_name = _collection_display_name(collection)
    offer_text = (
        f"💌 *Offer from {display_name(user)}*\n\n"
        f"{instance['model_emoji']} {col_name} #{model_number} "
        f"{_bg_emoji(background)} {_bg_label(background)}\n"
        f"Offer: {wrk_amount:,} WRK$\n"
        f"Market value: {market_price:,} WRK$\n\n"
        f"Offer expires in 24 hours."
    )
    offer_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"gift_offer:accept:{offer_id}:{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"gift_offer:decline:{offer_id}:{user.id}"),
    ]])

    try:
        await ctx.bot.send_message(target_id, offer_text, parse_mode="Markdown", reply_markup=offer_kb)
        await msg.reply_text(f"✅ Offer sent to {target_name}!")
    except TelegramError:
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await msg.reply_text(f"❌ Couldn't DM {target_name}. They need to start a conversation with the bot first.")


async def gift_offer_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1]
    offer_id = int(parts[2])
    from_user_id = int(parts[3])

    offer = await db.get_offer(config.DB_PATH, offer_id)
    if not offer:
        await query.answer("Offer no longer exists.", show_alert=True)
        return
    if offer["status"] != "pending":
        await query.answer(f"Offer already {offer['status']}.", show_alert=True)
        return
    if query.from_user.id != offer["to_user_id"]:
        await query.answer("This offer isn't for you.", show_alert=True)
        return

    instance = await db.get_gift_instance(config.DB_PATH, offer["instance_id"])

    if action == "decline":
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await query.answer()
        await query.edit_message_text("❌ Offer declined.")
        try:
            await ctx.bot.send_message(from_user_id, f"❌ Your offer was declined.")
        except TelegramError:
            pass
        return

    # Accept
    # Re-check ownership and balance
    if instance["owner_id"] != offer["to_user_id"]:
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await query.answer("You no longer own that gift.", show_alert=True)
        return

    seller_wallet = await db.get_wallet(config.DB_PATH, from_user_id)
    if not seller_wallet or seller_wallet["balance"] < offer["wrk_offered"]:
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await query.answer("The buyer no longer has enough WRK$.", show_alert=True)
        return

    # Execute trade
    await db.update_balance(config.DB_PATH, from_user_id, -offer["wrk_offered"])
    await db.update_balance(config.DB_PATH, offer["to_user_id"], offer["wrk_offered"])
    await db.transfer_gift(config.DB_PATH, offer["instance_id"], from_user_id)
    await db.update_offer_status(config.DB_PATH, offer_id, "accepted")

    col_name = _collection_display_name(instance["collection"])
    gift_label = f"{instance['model_emoji']} {col_name} #{instance['model_number']} {_bg_emoji(instance['background'])}"

    await query.answer()
    await query.edit_message_text(f"✅ Trade complete! You sold {gift_label} for {offer['wrk_offered']:,} WRK$.")
    try:
        await ctx.bot.send_message(
            from_user_id,
            f"✅ Trade accepted! You received {gift_label}.\nPaid: {offer['wrk_offered']:,} WRK$"
        )
    except TelegramError:
        pass


# ── /offers ───────────────────────────────────────────────────────────────────

async def cmd_offers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    offers = await db.get_offers_for_user(config.DB_PATH, user.id)
    if not offers:
        await msg.reply_text("📭 No pending offers.")
        return

    lines = ["📬 *Pending Offers*\n"]
    for o in offers:
        col_name = _collection_display_name(o["collection"])
        direction = "→ you" if o["to_user_id"] == user.id else "from you"
        lines.append(
            f"{o['model_emoji']} {col_name} #{o['model_number']} "
            f"{_bg_emoji(o['background'])} — {o['wrk_offered']:,} WRK$ ({direction})"
        )
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add handlers/gifts.py
git commit -m "feat: /offer P2P trading and /offers list"
```

---

## Task 7: Daily Drop Integration

**Files:**
- Modify: `handlers/economy.py`

- [ ] **Step 1: Find the daily reward message in `cmd_daily` (around line 192)**

The section that builds the reply text currently ends with:
```python
    await update.effective_message.reply_text(
        f"✅ Daily claimed! +{earned:,} WRK${bonus_note}\n"
        f"🔥 Streak: {streak} day(s)\n"
        f"💰 {new_balance:,} WRK${next_milestone}"
    )
```

- [ ] **Step 2: Replace with the gift drop version**

```python
    # 25% chance at a random low-tier gift drop
    gift_line = ""
    if await db.is_gifts_seeded(config.DB_PATH) and random.random() < 0.25:
        dropped = await db.get_random_low_tier_bank_gift(config.DB_PATH)
        if dropped:
            await db.transfer_gift(config.DB_PATH, dropped["id"], user.id)
            from handlers.gifts import _collection_display_name, _bg_emoji, _bg_label
            col_name = _collection_display_name(dropped["collection"])
            gift_line = (
                f"\n\n🎁 *Gift Drop!*\n"
                f"{dropped['model_emoji']} {col_name} #{dropped['model_number']} "
                f"{_bg_emoji(dropped['background'])} {_bg_label(dropped['background'])}"
            )

    await update.effective_message.reply_text(
        f"✅ Daily claimed! +{earned:,} WRK${bonus_note}\n"
        f"🔥 Streak: {streak} day(s)\n"
        f"💰 {new_balance:,} WRK${next_milestone}{gift_line}",
        parse_mode="Markdown"
    )
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add handlers/economy.py
git commit -m "feat: 25% daily gift drop for low-tier gifts"
```

---

## Task 8: Daily Price Update Job

**Files:**
- Modify: `jobs.py`

- [ ] **Step 1: Add `daily_price_update` to `jobs.py`**

Append after `sweep_punishments`:

```python
async def daily_price_update(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs at midnight: apply drift + demand pressure, then reset pressure."""
    import random, math
    prices = await db.get_all_gift_prices(config.DB_PATH)

    for p in prices:
        base = p["base_price"]
        current = p["current_price"]
        floor_price = int(base * 0.40)
        ceil_price = int(base * 5.0)

        # Random drift ±5–20%
        drift_pct = random.uniform(-0.20, 0.20)

        # Demand pressure: each net buy = +3%, net sell = -2%, capped ±30%
        demand = p["demand_pressure"]
        if demand > 0:
            demand_pct = min(demand * 0.03, 0.30)
        elif demand < 0:
            demand_pct = max(demand * 0.02, -0.30)
        else:
            demand_pct = 0.0

        new_price = int(current * (1 + drift_pct + demand_pct))
        new_price = max(floor_price, min(ceil_price, new_price))

        await db.update_gift_price(config.DB_PATH, p["collection"], p["background"], new_price)

    await db.reset_demand_pressure(config.DB_PATH)
    log.info("daily_price_update: updated %d price rows", len(prices))
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add jobs.py
git commit -m "feat: daily gift price update job with drift and demand pressure"
```

---

## Task 9: Register Commands in bot.py

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add gift imports to `bot.py`**

Add after the economy import block:

```python
from handlers.gifts import (
    cmd_seedgifts,
    cmd_inventory, cmd_gift,
    cmd_shop, cmd_buy, cmd_sell,
    cmd_offer, cmd_offers,
    gifts_callback, gift_offer_callback,
)
```

- [ ] **Step 2: Register command handlers in `build_app()`**

Add after the economy command handlers (before the `CallbackQueryHandler` lines):

```python
    app.add_handler(CommandHandler("seedgifts",  cmd_seedgifts))
    app.add_handler(CommandHandler("inventory",  cmd_inventory))
    app.add_handler(CommandHandler("inv",        cmd_inventory))
    app.add_handler(CommandHandler("gift",       cmd_gift))
    app.add_handler(CommandHandler("shop",       cmd_shop))
    app.add_handler(CommandHandler("buy",        cmd_buy))
    app.add_handler(CommandHandler("sell",       cmd_sell))
    app.add_handler(CommandHandler("offer",      cmd_offer))
    app.add_handler(CommandHandler("offers",     cmd_offers))
```

Add callback handlers alongside the existing ones:

```python
    app.add_handler(CallbackQueryHandler(gifts_callback,      pattern=r"^gifts:"))
    app.add_handler(CallbackQueryHandler(gift_offer_callback, pattern=r"^gift_offer:"))
```

- [ ] **Step 3: Register the daily price job in `build_app()`**

Add after `app.job_queue.run_repeating(sweep_punishments, ...)`:

```python
    app.job_queue.run_daily(daily_price_update, time=datetime.time(hour=0, minute=0))
```

Add to the imports at the top of `bot.py`:

```python
import datetime
from jobs import sweep_punishments, daily_price_update
```

- [ ] **Step 4: Smoke test**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -c "from bot import build_app; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 6: Commit and push**

```bash
git add bot.py
git commit -m "feat: register gift commands and daily price job in bot"
git push
```

---

## Task 10: Deploy and Seed

- [ ] **Step 1: Pull and restart on Pi**

```bash
cd ~/wrkshelperbot && git pull && systemctl --user restart wrkshelperbot
```

- [ ] **Step 2: Seed the gift catalog**

Send `/seedgifts` to the bot (you must be the OWNER_ID). Wait for the confirmation message.

Expected reply:
```
✅ Gift catalog seeded.
109 collections · XXXX unique instances
```

- [ ] **Step 3: Smoke test all commands**

- `/inv` → shows pageable inventory (empty until you get gifts)
- `/daily` → claim daily; check for occasional gift drop
- `/shop scared_cat` → lists Scared Cat models with prices
- `/buy scared_cat 1 orange` → purchases (if you have enough WRK$)
- `/inv` → shows the purchased gift
- Tap the gift in inventory → shows detail card with Back button
- `/gift scared_cat 1 orange` → posts flex card in chat
- `/sell scared_cat 1 orange` → sells back to bank at 80%
- `/offer @someone 1000 for scared_cat 1 orange` → sends DM offer
- `/offers` → lists pending offers
