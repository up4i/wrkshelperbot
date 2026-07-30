import time
import aiosqlite

from collectibles import (
    ANON_FIREWALL_COOLDOWN,
    ANON_MAX_SUFFIX,
    ANON_MIN_SUFFIX,
    anon_number_price,
    format_anon_number,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    log_channel_id INTEGER,
    warn_limit INTEGER DEFAULT 3,
    warn_action TEXT DEFAULT 'mute',
    warn_mute_duration INTEGER DEFAULT 3600,
    default_mute_duration INTEGER
);
CREATE TABLE IF NOT EXISTS warnings (
    chat_id INTEGER,
    user_id INTEGER,
    count INTEGER DEFAULT 0,
    last_reason TEXT,
    last_warned_at INTEGER,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS punishments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    action TEXT,
    expires_at INTEGER
);
CREATE TABLE IF NOT EXISTS halo_users (
    chat_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS user_activity (
    chat_id INTEGER,
    user_id INTEGER,
    username TEXT,
    full_name TEXT,
    last_seen INTEGER,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS autoreplies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    trigger TEXT COLLATE NOCASE,
    response_type TEXT,
    response_content TEXT,
    response_caption TEXT,
    UNIQUE(chat_id, trigger)
);
CREATE TABLE IF NOT EXISTS blocklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    pattern TEXT COLLATE NOCASE,
    UNIQUE(chat_id, pattern)
);
CREATE TABLE IF NOT EXISTS economy (
    user_id                    INTEGER PRIMARY KEY,
    username                   TEXT,
    full_name                  TEXT,
    balance                    INTEGER NOT NULL DEFAULT 1000,
    streak                     INTEGER NOT NULL DEFAULT 0,
    last_daily                 INTEGER NOT NULL DEFAULT 0,
    secure_vault_balance       INTEGER NOT NULL DEFAULT 0,
    vault_pending_amount       INTEGER NOT NULL DEFAULT 0,
    vault_withdraw_available_at INTEGER NOT NULL DEFAULT 0,
    anon_mask_enabled          INTEGER NOT NULL DEFAULT 0,
    anon_firewall_used_at      INTEGER NOT NULL DEFAULT 0,
    heat                       INTEGER NOT NULL DEFAULT 0,
    heat_updated_at            INTEGER NOT NULL DEFAULT 0
);
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
    gift_number INTEGER,
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
    id              INTEGER PRIMARY KEY,
    from_user_id    INTEGER NOT NULL,
    to_user_id      INTEGER NOT NULL,
    instance_id     INTEGER REFERENCES gift_instances(id),
    wrk_offered     INTEGER NOT NULL DEFAULT 0,
    request_gift_id INTEGER REFERENCES gift_instances(id),
    request_wrk     INTEGER NOT NULL DEFAULT 0,
    offer_anon_id   INTEGER REFERENCES anon_numbers(id),
    request_anon_id INTEGER REFERENCES anon_numbers(id),
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gift_market_listings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    gift_id    INTEGER NOT NULL REFERENCES gift_instances(id),
    seller_id  INTEGER NOT NULL,
    price      INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    buyer_id   INTEGER,
    created_at INTEGER NOT NULL,
    sold_at    INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gift_market_active
ON gift_market_listings(gift_id) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS anon_numbers (
    id          INTEGER PRIMARY KEY,
    suffix      INTEGER NOT NULL UNIQUE,
    price       INTEGER NOT NULL,
    owner_id    INTEGER,
    acquired_at INTEGER
);
CREATE TABLE IF NOT EXISTS anon_security_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail     TEXT NOT NULL,
    amount     INTEGER NOT NULL DEFAULT 0,
    actor_id   INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anon_security_events_user
ON anon_security_events(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS underground_bounties (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id           INTEGER NOT NULL,
    target_id            INTEGER NOT NULL,
    amount               INTEGER NOT NULL,
    fee                  INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'open',
    creator_alias        TEXT NOT NULL,
    creator_anon         INTEGER NOT NULL DEFAULT 0,
    target_alias         TEXT NOT NULL,
    target_anon          INTEGER NOT NULL DEFAULT 0,
    hunter_id            INTEGER,
    hunter_alias         TEXT,
    hunter_anon          INTEGER NOT NULL DEFAULT 0,
    challenge_sequence   TEXT,
    challenge_started_at INTEGER,
    challenge_expires_at INTEGER,
    created_at           INTEGER NOT NULL,
    expires_at           INTEGER NOT NULL,
    resolved_at          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_underground_bounties_status
ON underground_bounties(status, expires_at, amount DESC);
CREATE INDEX IF NOT EXISTS idx_underground_bounties_users
ON underground_bounties(creator_id, target_id, hunter_id);
CREATE TABLE IF NOT EXISTS underground_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bounty_id  INTEGER NOT NULL,
    hunter_id  INTEGER NOT NULL,
    outcome    TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(bounty_id, hunter_id)
);
CREATE TABLE IF NOT EXISTS underground_inventory (
    user_id    INTEGER NOT NULL,
    item_key   TEXT NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, item_key)
);
CREATE TABLE IF NOT EXISTS black_market_purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    item_key   TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    total_paid INTEGER NOT NULL,
    buyer_alias TEXT NOT NULL,
    buyer_anon INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS heists (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    heist_key        TEXT NOT NULL,
    leader_id        INTEGER NOT NULL,
    leader_role      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'forming',
    stake_per_member INTEGER NOT NULL,
    base_payout      INTEGER NOT NULL DEFAULT 0,
    payout_bonus     INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL,
    started_at       INTEGER,
    expires_at       INTEGER NOT NULL,
    resolved_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_heists_status
ON heists(status, expires_at);
CREATE TABLE IF NOT EXISTS heist_members (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    heist_id          INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    role              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'invited',
    alias             TEXT NOT NULL,
    anonymous         INTEGER NOT NULL DEFAULT 0,
    stake_paid        INTEGER NOT NULL DEFAULT 0,
    task_status       TEXT NOT NULL DEFAULT 'pending',
    challenge_json    TEXT,
    challenge_started_at INTEGER,
    challenge_expires_at INTEGER,
    task_result       TEXT,
    joined_at         INTEGER,
    UNIQUE(heist_id, user_id),
    UNIQUE(heist_id, role)
);
CREATE INDEX IF NOT EXISTS idx_heist_members_user
ON heist_members(user_id, status);
CREATE TABLE IF NOT EXISTS work_sessions (
    user_id         INTEGER PRIMARY KEY,
    taps            INTEGER NOT NULL DEFAULT 0,
    earned          INTEGER NOT NULL DEFAULT 0,
    started_at      INTEGER NOT NULL,
    job_tier_index  INTEGER NOT NULL DEFAULT 0,
    tap_count_start INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS game_stats (
    user_id         INTEGER PRIMARY KEY,
    slots_won       INTEGER NOT NULL DEFAULT 0,
    slots_lost      INTEGER NOT NULL DEFAULT 0,
    coinflip_won    INTEGER NOT NULL DEFAULT 0,
    coinflip_lost   INTEGER NOT NULL DEFAULT 0,
    blackjack_won   INTEGER NOT NULL DEFAULT 0,
    blackjack_lost  INTEGER NOT NULL DEFAULT 0,
    crash_won       INTEGER NOT NULL DEFAULT 0,
    crash_lost      INTEGER NOT NULL DEFAULT 0,
    crash_best_mult REAL    NOT NULL DEFAULT 0,
    duck_won        INTEGER NOT NULL DEFAULT 0,
    duck_lost       INTEGER NOT NULL DEFAULT 0,
    marbles_won     INTEGER NOT NULL DEFAULT 0,
    marbles_lost    INTEGER NOT NULL DEFAULT 0,
    livebj_won      INTEGER NOT NULL DEFAULT 0,
    livebj_lost     INTEGER NOT NULL DEFAULT 0,
    poker_won       INTEGER NOT NULL DEFAULT 0,
    poker_lost      INTEGER NOT NULL DEFAULT 0,
    roulette_won    INTEGER NOT NULL DEFAULT 0,
    roulette_lost   INTEGER NOT NULL DEFAULT 0,
    plinko_won      INTEGER NOT NULL DEFAULT 0,
    plinko_lost     INTEGER NOT NULL DEFAULT 0,
    wheel_won       INTEGER NOT NULL DEFAULT 0,
    wheel_lost      INTEGER NOT NULL DEFAULT 0,
    slider_won      INTEGER NOT NULL DEFAULT 0,
    slider_lost     INTEGER NOT NULL DEFAULT 0,
    craps_won       INTEGER NOT NULL DEFAULT 0,
    craps_lost      INTEGER NOT NULL DEFAULT 0,
    highlow_won     INTEGER NOT NULL DEFAULT 0,
    highlow_lost    INTEGER NOT NULL DEFAULT 0,
    cases_won       INTEGER NOT NULL DEFAULT 0,
    cases_lost      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bot_roles (
    user_id INTEGER PRIMARY KEY,
    role    TEXT NOT NULL
);
"""

async def _migrate(db) -> None:
    # economy table migrations
    async with db.execute("PRAGMA table_info(economy)") as cur:
        econ_cols = {row[1] async for row in cur}
    econ_new = {
        "last_work":           "INTEGER NOT NULL DEFAULT 0",
        "last_beg":            "INTEGER NOT NULL DEFAULT 0",
        "work_count":          "INTEGER NOT NULL DEFAULT 0",
        "work_reminder":       "INTEGER NOT NULL DEFAULT 0",
        "last_reminder_sent":  "INTEGER NOT NULL DEFAULT 0",
        "pinned_gift_id":      "INTEGER",
        "pinned_anon_id":      "INTEGER",
        "pinned_stat":         "TEXT NOT NULL DEFAULT 'crash_mult'",
        "photo_url":           "TEXT",
        "secure_vault_balance": "INTEGER NOT NULL DEFAULT 0",
        "vault_pending_amount": "INTEGER NOT NULL DEFAULT 0",
        "vault_withdraw_available_at": "INTEGER NOT NULL DEFAULT 0",
        "anon_mask_enabled":    "INTEGER NOT NULL DEFAULT 0",
        "anon_firewall_used_at": "INTEGER NOT NULL DEFAULT 0",
        "heat":                  "INTEGER NOT NULL DEFAULT 0",
        "heat_updated_at":       "INTEGER NOT NULL DEFAULT 0",
    }
    for col, typedef in econ_new.items():
        if col not in econ_cols:
            await db.execute(f"ALTER TABLE economy ADD COLUMN {col} {typedef}")
            await db.commit()

    # last_rob / last_hack cooldown columns
    for col in ("last_rob INTEGER NOT NULL DEFAULT 0", "last_hack INTEGER NOT NULL DEFAULT 0"):
        col_name = col.split()[0]
        if col_name not in econ_cols:
            await db.execute(f"ALTER TABLE economy ADD COLUMN {col}")
            await db.commit()

    async with db.execute("PRAGMA table_info(game_stats)") as cur:
        game_stat_cols = {row[1] async for row in cur}
    for col in (
        "duck_won", "duck_lost", "marbles_won", "marbles_lost",
        "livebj_won", "livebj_lost", "poker_won", "poker_lost",
        "roulette_won", "roulette_lost", "plinko_won", "plinko_lost",
        "wheel_won", "wheel_lost", "slider_won", "slider_lost",
        "craps_won", "craps_lost", "highlow_won", "highlow_lost",
        "cases_won", "cases_lost",
    ):
        if col not in game_stat_cols:
            await db.execute(
                f"ALTER TABLE game_stats ADD COLUMN {col} "
                "INTEGER NOT NULL DEFAULT 0"
            )
    await db.commit()

    async with db.execute("PRAGMA table_info(gift_instances)") as cur:
        gift_cols = {row[1] async for row in cur}
    gift_new = {
        "sort_index":    "INTEGER",
        "staked":        "INTEGER DEFAULT 0",
        "is_admin_gift": "INTEGER DEFAULT 0",
    }
    for col, typedef in gift_new.items():
        if col not in gift_cols:
            await db.execute(f"ALTER TABLE gift_instances ADD COLUMN {col} {typedef}")
            await db.commit()

    # Trades originally required an offered gift. Rebuild older tables so a
    # WRK$-only or anonymous-number-only trade can be stored as well.
    async with db.execute("PRAGMA table_info(gift_offers)") as cur:
        trade_info = [row async for row in cur]
    trade_cols = {row[1]: row for row in trade_info}
    instance_col = trade_cols.get("instance_id")
    if instance_col and instance_col[3]:
        await db.execute("DROP TABLE IF EXISTS gift_offers_trade_migration")
        await db.execute("""CREATE TABLE gift_offers_trade_migration (
            id              INTEGER PRIMARY KEY,
            from_user_id    INTEGER NOT NULL,
            to_user_id      INTEGER NOT NULL,
            instance_id     INTEGER REFERENCES gift_instances(id),
            wrk_offered     INTEGER NOT NULL DEFAULT 0,
            request_gift_id INTEGER REFERENCES gift_instances(id),
            request_wrk     INTEGER NOT NULL DEFAULT 0,
            offer_anon_id   INTEGER REFERENCES anon_numbers(id),
            request_anon_id INTEGER REFERENCES anon_numbers(id),
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      INTEGER NOT NULL
        )""")
        copy_expr = {
            "id": "id",
            "from_user_id": "from_user_id",
            "to_user_id": "to_user_id",
            "instance_id": "instance_id",
            "wrk_offered": "wrk_offered",
            "request_gift_id": "request_gift_id" if "request_gift_id" in trade_cols else "NULL",
            "request_wrk": "request_wrk" if "request_wrk" in trade_cols else "0",
            "offer_anon_id": "offer_anon_id" if "offer_anon_id" in trade_cols else "NULL",
            "request_anon_id": "request_anon_id" if "request_anon_id" in trade_cols else "NULL",
            "status": "status",
            "created_at": "created_at",
        }
        names = ", ".join(copy_expr)
        values = ", ".join(copy_expr.values())
        await db.execute(
            f"INSERT INTO gift_offers_trade_migration ({names}) "
            f"SELECT {values} FROM gift_offers"
        )
        await db.execute("DROP TABLE gift_offers")
        await db.execute(
            "ALTER TABLE gift_offers_trade_migration RENAME TO gift_offers"
        )
        await db.commit()
    else:
        for col, typedef in {
            "request_gift_id": "INTEGER REFERENCES gift_instances(id)",
            "request_wrk": "INTEGER NOT NULL DEFAULT 0",
            "offer_anon_id": "INTEGER REFERENCES anon_numbers(id)",
            "request_anon_id": "INTEGER REFERENCES anon_numbers(id)",
        }.items():
            if col not in trade_cols:
                await db.execute(
                    f"ALTER TABLE gift_offers ADD COLUMN {col} {typedef}"
                )
        await db.commit()

    # hack_sessions table (shared with mini-app)
    await db.execute("""CREATE TABLE IF NOT EXISTS hack_sessions (
        user_id          INTEGER PRIMARY KEY,
        word             TEXT    NOT NULL,
        clue             TEXT    NOT NULL,
        reward           INTEGER NOT NULL,
        attempts         INTEGER NOT NULL DEFAULT 5,
        revealed_indices TEXT    NOT NULL DEFAULT '0',
        started_at       INTEGER NOT NULL
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS gift_market_listings (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        gift_id    INTEGER NOT NULL REFERENCES gift_instances(id),
        seller_id  INTEGER NOT NULL,
        price      INTEGER NOT NULL,
        status     TEXT NOT NULL DEFAULT 'active',
        buyer_id   INTEGER,
        created_at INTEGER NOT NULL,
        sold_at    INTEGER
    )""")
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_gift_market_active
        ON gift_market_listings(gift_id) WHERE status = 'active'""")
    await db.execute("""CREATE TABLE IF NOT EXISTS anon_numbers (
        id          INTEGER PRIMARY KEY,
        suffix      INTEGER NOT NULL UNIQUE,
        price       INTEGER NOT NULL,
        owner_id    INTEGER,
        acquired_at INTEGER
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS anon_security_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        detail     TEXT NOT NULL,
        amount     INTEGER NOT NULL DEFAULT 0,
        actor_id   INTEGER,
        created_at INTEGER NOT NULL
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_anon_security_events_user
        ON anon_security_events(user_id, created_at DESC)""")
    await db.execute("""CREATE TABLE IF NOT EXISTS underground_bounties (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        creator_id           INTEGER NOT NULL,
        target_id            INTEGER NOT NULL,
        amount               INTEGER NOT NULL,
        fee                  INTEGER NOT NULL DEFAULT 0,
        status               TEXT NOT NULL DEFAULT 'open',
        creator_alias        TEXT NOT NULL,
        creator_anon         INTEGER NOT NULL DEFAULT 0,
        target_alias         TEXT NOT NULL,
        target_anon          INTEGER NOT NULL DEFAULT 0,
        hunter_id            INTEGER,
        hunter_alias         TEXT,
        hunter_anon          INTEGER NOT NULL DEFAULT 0,
        challenge_sequence   TEXT,
        challenge_started_at INTEGER,
        challenge_expires_at INTEGER,
        created_at           INTEGER NOT NULL,
        expires_at           INTEGER NOT NULL,
        resolved_at          INTEGER
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_underground_bounties_status
        ON underground_bounties(status, expires_at, amount DESC)""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_underground_bounties_users
        ON underground_bounties(creator_id, target_id, hunter_id)""")
    await db.execute("""CREATE TABLE IF NOT EXISTS underground_attempts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        bounty_id  INTEGER NOT NULL,
        hunter_id  INTEGER NOT NULL,
        outcome    TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        UNIQUE(bounty_id, hunter_id)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS underground_inventory (
        user_id    INTEGER NOT NULL,
        item_key   TEXT NOT NULL,
        quantity   INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, item_key)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS black_market_purchases (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        item_key    TEXT NOT NULL,
        quantity    INTEGER NOT NULL,
        total_paid  INTEGER NOT NULL,
        buyer_alias TEXT NOT NULL,
        buyer_anon  INTEGER NOT NULL DEFAULT 0,
        created_at  INTEGER NOT NULL
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS heists (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        heist_key        TEXT NOT NULL,
        leader_id        INTEGER NOT NULL,
        leader_role      TEXT NOT NULL,
        status           TEXT NOT NULL DEFAULT 'forming',
        stake_per_member INTEGER NOT NULL,
        base_payout      INTEGER NOT NULL DEFAULT 0,
        payout_bonus     INTEGER NOT NULL DEFAULT 0,
        created_at       INTEGER NOT NULL,
        started_at       INTEGER,
        expires_at       INTEGER NOT NULL,
        resolved_at      INTEGER
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_heists_status
        ON heists(status, expires_at)""")
    await db.execute("""CREATE TABLE IF NOT EXISTS heist_members (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        heist_id             INTEGER NOT NULL,
        user_id              INTEGER NOT NULL,
        role                 TEXT NOT NULL,
        status               TEXT NOT NULL DEFAULT 'invited',
        alias                TEXT NOT NULL,
        anonymous            INTEGER NOT NULL DEFAULT 0,
        stake_paid           INTEGER NOT NULL DEFAULT 0,
        task_status          TEXT NOT NULL DEFAULT 'pending',
        challenge_json       TEXT,
        challenge_started_at INTEGER,
        challenge_expires_at INTEGER,
        task_result          TEXT,
        joined_at            INTEGER,
        UNIQUE(heist_id, user_id),
        UNIQUE(heist_id, role)
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_heist_members_user
        ON heist_members(user_id, status)""")
    await db.executemany(
        "INSERT OR IGNORE INTO anon_numbers (id, suffix, price) VALUES (?, ?, ?)",
        [
            (suffix, suffix, anon_number_price(suffix))
            for suffix in range(ANON_MIN_SUFFIX, ANON_MAX_SUFFIX + 1)
        ],
    )
    await db.commit()

    # groups table migrations
    async with db.execute("PRAGMA table_info(groups)") as cur:
        cols = {row[1] async for row in cur}
    new_cols = {
        "rules": "TEXT",
        "clean_service_msgs": "INTEGER DEFAULT 0",
        "welcome_text": "TEXT",
        "welcome_enabled": "INTEGER DEFAULT 1",
        "goodbye_text": "TEXT",
        "goodbye_enabled": "INTEGER DEFAULT 1",
        "flood_limit": "INTEGER DEFAULT 0",
        "flood_window": "INTEGER DEFAULT 30",
        "flood_action": "TEXT DEFAULT 'mute'",
        "flood_mute_duration": "INTEGER DEFAULT 600",
        "blocklist_action": "TEXT DEFAULT 'delete'",
        "locks": "TEXT",
        "antiraid_enabled": "INTEGER DEFAULT 0",
        "antiraid_limit": "INTEGER DEFAULT 5",
        "antiraid_window": "INTEGER DEFAULT 30",
        "antiraid_mute_duration": "INTEGER DEFAULT 600",
        "bot_topic_id": "INTEGER",
    }
    for col, typedef in new_cols.items():
        if col not in cols:
            await db.execute(f"ALTER TABLE groups ADD COLUMN {col} {typedef}")
            await db.commit()

async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        await _migrate(db)

async def upsert_group(db_path: str, chat_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR IGNORE INTO groups (chat_id) VALUES (?)", (chat_id,))
        await db.commit()

async def get_group(db_path: str, chat_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM groups WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def update_group(db_path: str, chat_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [chat_id]
    async with aiosqlite.connect(db_path) as db:
        await db.execute(f"UPDATE groups SET {cols} WHERE chat_id = ?", vals)
        await db.commit()

async def add_warning(db_path: str, chat_id: int, user_id: int, reason: str) -> int:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO warnings (chat_id, user_id, count, last_reason, last_warned_at)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                   count = count + 1,
                   last_reason = excluded.last_reason,
                   last_warned_at = excluded.last_warned_at""",
            (chat_id, user_id, reason, now),
        )
        await db.commit()
        async with db.execute(
            "SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0]

async def get_warnings(db_path: str, chat_id: int, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def reset_warnings(db_path: str, chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        )
        await db.commit()

async def add_punishment(db_path: str, chat_id: int, user_id: int, action: str, expires_at: int | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO punishments (chat_id, user_id, action, expires_at) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, action, expires_at),
        )
        await db.commit()

async def remove_punishment(db_path: str, chat_id: int, user_id: int, action: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM punishments WHERE chat_id = ? AND user_id = ? AND action = ?",
            (chat_id, user_id, action),
        )
        await db.commit()

async def get_expired_punishments(db_path: str) -> list[dict]:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM punishments WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
        ) as cur:
            return [dict(r) async for r in cur]

async def delete_punishment_by_id(db_path: str, punishment_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM punishments WHERE id = ?", (punishment_id,))
        await db.commit()

# --- halo ---

async def give_halo(db_path: str, chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO halo_users (chat_id, user_id) VALUES (?, ?)",
            (chat_id, user_id),
        )
        await db.commit()

async def remove_halo(db_path: str, chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM halo_users WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        await db.commit()

async def get_halos(db_path: str, chat_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT h.user_id, a.full_name, a.username
               FROM halo_users h
               LEFT JOIN user_activity a ON a.chat_id = h.chat_id AND a.user_id = h.user_id
               WHERE h.chat_id = ?""",
            (chat_id,),
        ) as cur:
            return [dict(r) async for r in cur]


async def has_halo(db_path: str, chat_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT 1 FROM halo_users WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None

# --- user activity ---

async def update_activity(
    db_path: str, chat_id: int, user_id: int, username: str | None, full_name: str
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO user_activity (chat_id, user_id, username, full_name, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                   username = excluded.username,
                   full_name = excluded.full_name,
                   last_seen = excluded.last_seen""",
            (chat_id, user_id, username, full_name, now),
        )
        await db.commit()

# --- blocklist ---

async def add_blocked_pattern(db_path: str, chat_id: int, pattern: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO blocklist (chat_id, pattern) VALUES (?, ?)",
            (chat_id, pattern),
        )
        await db.commit()

async def remove_blocked_pattern(db_path: str, chat_id: int, pattern: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM blocklist WHERE chat_id = ? AND pattern = ?",
            (chat_id, pattern),
        )
        await db.commit()
        return cur.rowcount > 0

async def get_blocklist(db_path: str, chat_id: int) -> list[str]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT pattern FROM blocklist WHERE chat_id = ? ORDER BY pattern", (chat_id,)
        ) as cur:
            return [row[0] async for row in cur]

# --- autoreplies ---

async def add_autoreply(
    db_path: str, chat_id: int, trigger: str, response_type: str,
    response_content: str, response_caption: str | None = None,
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO autoreplies (chat_id, trigger, response_type, response_content, response_caption)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, trigger) DO UPDATE SET
                   response_type = excluded.response_type,
                   response_content = excluded.response_content,
                   response_caption = excluded.response_caption""",
            (chat_id, trigger, response_type, response_content, response_caption),
        )
        await db.commit()

async def remove_autoreply(db_path: str, chat_id: int, trigger: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM autoreplies WHERE chat_id = ? AND trigger = ?",
            (chat_id, trigger),
        )
        await db.commit()
        return cur.rowcount > 0

async def get_autoreplies(db_path: str, chat_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM autoreplies WHERE chat_id = ? ORDER BY trigger", (chat_id,)
        ) as cur:
            return [dict(r) async for r in cur]

async def get_user_by_username(db_path: str, chat_id: int, username: str) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, full_name FROM user_activity WHERE chat_id = ? AND LOWER(username) = LOWER(?)",
            (chat_id, username.lstrip("@")),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_user_by_username_global(db_path: str, username: str) -> dict | None:
    """Fallback lookup in the economy table (not chat-scoped)."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, full_name, username FROM economy WHERE LOWER(username) = LOWER(?)",
            (username.lstrip("@"),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# --- roles ---

async def is_eco_admin(db_path: str, user_id: int, owner_id: int) -> bool:
    if user_id == owner_id:
        return True
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT 1 FROM bot_roles WHERE user_id = ? AND role = 'ecoadmin'", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def add_eco_admin(db_path: str, user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bot_roles (user_id, role) VALUES (?, 'ecoadmin')", (user_id,)
        )
        await db.commit()


async def remove_eco_admin(db_path: str, user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM bot_roles WHERE user_id = ? AND role = 'ecoadmin'", (user_id,)
        )
        await db.commit()


async def get_user_badges(db_path: str, user_id: int, owner_id: int) -> list[str]:
    badges = []
    if user_id == owner_id:
        badges.append("owner")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT role FROM bot_roles WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                badges.append(row[0])
        async with db.execute(
            """SELECT gi.owner_id FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gm.collection = 'plush_pepe' AND gi.gift_number = 1
               LIMIT 1"""
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] == user_id:
                badges.append("plush_pepe_1")
    return badges


async def list_eco_admins(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT br.user_id, COALESCE(e.full_name, e.username, CAST(br.user_id AS TEXT)) AS name
               FROM bot_roles br LEFT JOIN economy e ON e.user_id = br.user_id
               WHERE br.role = 'ecoadmin'"""
        ) as cur:
            return [dict(r) async for r in cur]


async def get_inactives(db_path: str, chat_id: int, since_ts: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_id, username, full_name, last_seen
               FROM user_activity
               WHERE chat_id = ? AND last_seen < ?
               ORDER BY last_seen ASC""",
            (chat_id, since_ts),
        ) as cur:
            return [dict(r) async for r in cur]


# --- economy ---

async def upsert_wallet(db_path: str, user_id: int, username: str | None, full_name: str | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO economy (user_id, username, full_name, balance, streak, last_daily)
               VALUES (?, ?, ?, 1000, 0, 0)
               ON CONFLICT(user_id) DO UPDATE SET
                   username  = COALESCE(excluded.username,  economy.username),
                   full_name = COALESCE(excluded.full_name, economy.full_name)""",
            (user_id, username, full_name),
        )
        await db.commit()


async def get_wallet(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, full_name, balance, streak, last_daily, last_work, last_beg, work_count FROM economy WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_balance(db_path: str, user_id: int, delta: int) -> int | None:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "UPDATE economy SET balance = MAX(0, balance + ?) WHERE user_id = ? RETURNING balance",
            (delta, user_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return row[0] if row else None


async def record_game_stats(
    db_path: str,
    user_id: int,
    *,
    slots_won: int = 0,
    slots_lost: int = 0,
    coinflip_won: int = 0,
    coinflip_lost: int = 0,
    blackjack_won: int = 0,
    blackjack_lost: int = 0,
    crash_won: int = 0,
    crash_lost: int = 0,
    crash_mult: float = 0.0,
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO game_stats
               (user_id, slots_won, slots_lost, coinflip_won, coinflip_lost,
                blackjack_won, blackjack_lost, crash_won, crash_lost, crash_best_mult)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   slots_won       = slots_won       + excluded.slots_won,
                   slots_lost      = slots_lost      + excluded.slots_lost,
                   coinflip_won    = coinflip_won    + excluded.coinflip_won,
                   coinflip_lost   = coinflip_lost   + excluded.coinflip_lost,
                   blackjack_won   = blackjack_won   + excluded.blackjack_won,
                   blackjack_lost  = blackjack_lost  + excluded.blackjack_lost,
                   crash_won       = crash_won       + excluded.crash_won,
                   crash_lost      = crash_lost      + excluded.crash_lost,
                   crash_best_mult = MAX(crash_best_mult, excluded.crash_best_mult)""",
            (user_id, slots_won, slots_lost, coinflip_won, coinflip_lost,
             blackjack_won, blackjack_lost, crash_won, crash_lost, crash_mult),
        )
        await db.commit()


async def get_leaderboard(db_path: str, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT e.user_id, e.balance,
                      COALESCE(a.full_name, e.full_name) AS full_name,
                      COALESCE(a.username, e.username) AS username
               FROM economy e
               LEFT JOIN (
                   SELECT user_id, full_name, username
                   FROM user_activity
                   WHERE (user_id, last_seen) IN (
                       SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
                   )
               ) a ON a.user_id = e.user_id
               ORDER BY e.balance DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) async for r in cur]


_STAT_COLS = {
    "balance":   ("e.balance",                                                                   "WRK$"),
    "streak":    ("e.streak",                                                                    "day streak"),
    "gifts":     ("COUNT(gi.id)",                                                                "gifts"),
    "gamble":    ("gs.slots_won+gs.coinflip_won+gs.blackjack_won+gs.crash_won",                 "WRK$ won"),
    "loss":      ("gs.slots_lost+gs.coinflip_lost+gs.blackjack_lost+gs.crash_lost",             "WRK$ lost"),
    "slots":     ("gs.slots_won",                                                                "WRK$ won"),
    "coinflip":  ("gs.coinflip_won",                                                             "WRK$ won"),
    "blackjack": ("gs.blackjack_won",                                                            "WRK$ won"),
    "crash":     ("gs.crash_won",                                                                "WRK$ won"),
    "mult":      ("gs.crash_best_mult",                                                          "× best mult"),
}

async def get_stats_leaderboard(db_path: str, tab: str, limit: int = 10) -> list[dict]:
    if tab not in _STAT_COLS:
        return []
    col, unit = _STAT_COLS[tab]
    name_sub = """LEFT JOIN (
                SELECT user_id, COALESCE(full_name,'') AS full_name, username
                FROM user_activity
                WHERE (user_id, last_seen) IN (
                    SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
                )
            ) a ON a.user_id = e.user_id"""

    if tab == "balance":
        sql = f"""SELECT e.user_id, ({col}) AS value,
                         COALESCE(a.username, e.username) AS username,
                         COALESCE(a.full_name, e.full_name) AS full_name
                  FROM economy e {name_sub}
                  ORDER BY value DESC LIMIT ?"""
        params = (limit,)
    elif tab == "streak":
        sql = f"""SELECT e.user_id, ({col}) AS value,
                         COALESCE(a.username, e.username) AS username,
                         COALESCE(a.full_name, e.full_name) AS full_name
                  FROM economy e {name_sub}
                  ORDER BY value DESC LIMIT ?"""
        params = (limit,)
    elif tab == "gifts":
        sql = f"""SELECT e.user_id, ({col}) AS value,
                         COALESCE(a.username, e.username) AS username,
                         COALESCE(a.full_name, e.full_name) AS full_name
                  FROM economy e
                  LEFT JOIN gift_instances gi ON gi.owner_id = e.user_id
                  {name_sub}
                  GROUP BY e.user_id ORDER BY value DESC LIMIT ?"""
        params = (limit,)
    else:
        sql = f"""SELECT e.user_id, ({col}) AS value,
                         COALESCE(a.username, e.username) AS username,
                         COALESCE(a.full_name, e.full_name) AS full_name
                  FROM game_stats gs
                  JOIN economy e ON e.user_id = gs.user_id
                  {name_sub.replace('ON a.user_id = e.user_id', 'ON a.user_id = gs.user_id')}
                  WHERE ({col}) > 0
                  ORDER BY value DESC LIMIT ?"""
        params = (limit,)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            item = {
                "user_id": row["user_id"],
                "value": row["value"],
                "username": row["username"],
                "full_name": row["full_name"],
                "unit": unit,
            }
            async with db.execute(
                """SELECT a.suffix FROM economy e
                   JOIN anon_numbers a
                     ON a.id = e.pinned_anon_id AND a.owner_id = e.user_id
                   WHERE e.user_id = ? AND e.anon_mask_enabled = 1""",
                (row["user_id"],),
            ) as alias_cur:
                alias = await alias_cur.fetchone()
            if alias:
                item["username"] = None
                item["full_name"] = format_anon_number(alias["suffix"])
            result.append(item)
        return result


async def get_profile(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT e.user_id, e.balance, e.streak, e.work_count,
                      e.pinned_gift_id, e.pinned_anon_id,
                      e.secure_vault_balance, e.vault_pending_amount,
                      e.anon_mask_enabled,
                      COALESCE(e.pinned_stat, 'crash_mult') AS pinned_stat,
                      COALESCE(a.username, e.username) AS username,
                      COALESCE(a.full_name, e.full_name) AS full_name
               FROM economy e
               LEFT JOIN (
                   SELECT user_id, username, full_name FROM user_activity
                   WHERE (user_id, last_seen) IN (
                       SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
                   )
               ) a ON a.user_id = e.user_id
               WHERE e.user_id = ?""", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)

        async with db.execute(
            "SELECT COUNT(*)+1 FROM economy WHERE balance > ?", (d["balance"],)
        ) as cur:
            d["balance_rank"] = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*)+1 FROM economy WHERE streak > ?", (d["streak"],)
        ) as cur:
            d["streak_rank"] = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM gift_instances WHERE owner_id = ?", (user_id,)
        ) as cur:
            d["gift_count"] = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*)+1 FROM (SELECT owner_id, COUNT(*) AS c FROM gift_instances "
            "WHERE owner_id IS NOT NULL GROUP BY owner_id HAVING c > ?)", (d["gift_count"],)
        ) as cur:
            d["gift_rank"] = (await cur.fetchone())[0]

        pinned = None
        if d.get("pinned_gift_id"):
            async with db.execute(
                "SELECT gi.id, gi.gift_number, gi.background, "
                "gm.collection, gm.model_name, gm.model_emoji, gm.custom_emoji_id "
                "FROM gift_instances gi JOIN gift_models gm ON gm.id = gi.model_id "
                "WHERE gi.id = ? AND gi.owner_id = ?", (d["pinned_gift_id"], user_id)
            ) as cur:
                pg = await cur.fetchone()
                pinned = dict(pg) if pg else None
        d["pinned_gift"] = pinned

        pinned_anon = None
        if d.get("pinned_anon_id"):
            async with db.execute(
                "SELECT id, suffix, price FROM anon_numbers "
                "WHERE id = ? AND owner_id = ?",
                (d["pinned_anon_id"], user_id),
            ) as cur:
                pa = await cur.fetchone()
                if pa:
                    pinned_anon = dict(pa)
                    pinned_anon["number"] = format_anon_number(pinned_anon["suffix"])
        d["pinned_anon"] = pinned_anon
        d["identity_masked"] = bool(d.get("anon_mask_enabled")) and bool(
            pinned_anon
        )

        gs_cols = ("slots_won","slots_lost","coinflip_won","coinflip_lost",
                   "blackjack_won","blackjack_lost","crash_won","crash_lost","crash_best_mult")
        async with db.execute(
            f"SELECT {','.join(gs_cols)} FROM game_stats WHERE user_id = ?", (user_id,)
        ) as cur:
            gs = await cur.fetchone()
        if gs:
            d["total_won"]  = gs["slots_won"] + gs["coinflip_won"] + gs["blackjack_won"] + gs["crash_won"]
            d["total_lost"] = gs["slots_lost"] + gs["coinflip_lost"] + gs["blackjack_lost"] + gs["crash_lost"]
            d["best_mult"]  = gs["crash_best_mult"]
        else:
            d["total_won"] = d["total_lost"] = d["best_mult"] = 0

        async with db.execute(
            """SELECT COALESCE(SUM(gp.current_price), 0) AS gift_value
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background
               WHERE gi.owner_id = ?""",
            (user_id,)
        ) as cur:
            row2 = await cur.fetchone()
            d["gift_value"] = row2[0] if row2 else 0
        async with db.execute(
            "SELECT COUNT(*) AS anon_count, COALESCE(SUM(price), 0) AS anon_value "
            "FROM anon_numbers WHERE owner_id = ?",
            (user_id,),
        ) as cur:
            anon_row = await cur.fetchone()
            d["anon_count"] = anon_row["anon_count"]
            d["anon_value"] = anon_row["anon_value"]
        d["vault_value"] = (
            d["secure_vault_balance"] + d["vault_pending_amount"]
        )
        d["net_worth"] = (
            d["balance"]
            + d["vault_value"]
            + d["gift_value"]
            + d["anon_value"]
        )

        async with db.execute(
            """SELECT COUNT(*)+1 FROM (
                   SELECT e.user_id,
                          e.balance
                          + COALESCE(e.secure_vault_balance, 0)
                          + COALESCE(e.vault_pending_amount, 0)
                          + COALESCE(gv.gift_value, 0)
                          + COALESCE(av.anon_value, 0) AS nw
                   FROM economy e
                   LEFT JOIN (
                       SELECT gi.owner_id, SUM(gp.current_price) AS gift_value
                       FROM gift_instances gi
                       JOIN gift_models gm ON gm.id = gi.model_id
                       JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background
                       GROUP BY gi.owner_id
                   ) gv ON gv.owner_id = e.user_id
                   LEFT JOIN (
                       SELECT owner_id, SUM(price) AS anon_value
                       FROM anon_numbers WHERE owner_id IS NOT NULL GROUP BY owner_id
                   ) av ON av.owner_id = e.user_id
               ) WHERE nw > ?""",
            (d["net_worth"],)
        ) as cur:
            d["networth_rank"] = (await cur.fetchone())[0]

        pinned_stat = d.get("pinned_stat", "crash_mult")
        if pinned_stat == "crash_mult":
            d["stat_highlight_label"] = "Best crash mult"
            bm = d.get("best_mult") or 0
            d["stat_highlight_value"] = f"{bm:.2f}×" if bm else "—"
        elif pinned_stat == "gamble_won":
            d["stat_highlight_label"] = "Total WRK$ won"
            d["stat_highlight_value"] = f'{d.get("total_won", 0):,} WRK$'
        elif pinned_stat == "gamble_lost":
            d["stat_highlight_label"] = "Total WRK$ lost"
            d["stat_highlight_value"] = f'{d.get("total_lost", 0):,} WRK$'
        elif pinned_stat == "gifts_owned":
            d["stat_highlight_label"] = "Gifts owned"
            d["stat_highlight_value"] = str(d.get("gift_count", 0))
        elif pinned_stat == "streak":
            d["stat_highlight_label"] = "Current streak"
            d["stat_highlight_value"] = f'{d.get("streak", 0)} days'
        else:
            d["stat_highlight_label"] = "Best crash mult"
            bm = d.get("best_mult") or 0
            d["stat_highlight_value"] = f"{bm:.2f}×" if bm else "—"

        return d


async def claim_work(db_path: str, user_id: int, amount: int, timestamp: int, taps: int = 1) -> tuple[int, int]:
    """Returns (new_balance, new_work_count)."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "UPDATE economy SET balance = balance + ?, last_work = ?, work_count = work_count + ? "
            "WHERE user_id = ? RETURNING balance, work_count",
            (amount, timestamp, taps, user_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return (row[0], row[1]) if row else (0, 0)


async def get_work_session(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def start_work_session(db_path: str, user_id: int, tap_count_start: int, job_tier_index: int) -> dict:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "INSERT OR REPLACE INTO work_sessions "
            "(user_id, taps, earned, started_at, job_tier_index, tap_count_start) "
            "VALUES (?, 0, 0, ?, ?, ?) RETURNING *",
            (user_id, now, job_tier_index, tap_count_start),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return dict(row)


async def sync_work_session(db_path: str, user_id: int, taps_delta: int, earned_delta: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "UPDATE work_sessions SET taps = taps + ?, earned = earned + ? "
            "WHERE user_id = ? RETURNING *",
            (taps_delta, earned_delta, user_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return dict(row) if row else None


async def end_work_session(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "DELETE FROM work_sessions WHERE user_id = ? RETURNING *", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return dict(row) if row else None


async def claim_beg(db_path: str, user_id: int, amount: int, timestamp: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "UPDATE economy SET balance = balance + ?, last_beg = ? WHERE user_id = ? RETURNING balance",
            (amount, timestamp, user_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return row[0] if row else 0


async def claim_daily(db_path: str, user_id: int, amount: int, streak: int, timestamp: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "UPDATE economy SET balance = balance + ?, streak = ?, last_daily = ? WHERE user_id = ? RETURNING balance",
            (amount, streak, timestamp, user_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return row[0] if row else 0


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
        # Assign per-collection sequential gift_numbers (ordered by model_number, then background tier)
        await db.execute("""
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
            """SELECT gi.id, gi.background, gi.gift_number, gi.acquired_at,
                      COALESCE(gi.is_admin_gift, 0) AS is_admin_gift,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.model_rarity_pct, gm.tier, gm.custom_emoji_id
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.owner_id = ?
               ORDER BY gm.collection, gi.gift_number""",
            (user_id,)
        ) as cur:
            return [dict(r) async for r in cur]


async def get_gift_instance(db_path: str, instance_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id, gi.background, gi.gift_number, gi.owner_id, gi.acquired_at,
                      COALESCE(gi.is_admin_gift, 0) AS is_admin_gift,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.model_rarity_pct, gm.tier, gm.custom_emoji_id
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.id = ?""",
            (instance_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_gift_instance_by_spec(
    db_path: str,
    collection: str,
    model_number: int,
    background: str,
) -> dict | None:
    """Look up a gift by its catalog attributes.

    Commands now identify gifts by their collection-wide gift number, but this
    compatibility lookup remains useful for catalog tooling and older callers.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id, gi.background, gi.gift_number, gi.owner_id, gi.acquired_at,
                      COALESCE(gi.is_admin_gift, 0) AS is_admin_gift,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.model_rarity_pct, gm.tier, gm.custom_emoji_id
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gm.collection = ? AND gm.model_number = ? AND gi.background = ?""",
            (collection, model_number, background),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_pinned_gift(db_path: str, user_id: int, gift_id: int | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE economy SET pinned_gift_id = ? WHERE user_id = ?", (gift_id, user_id)
        )
        await db.commit()


async def get_pinned_gift_id(db_path: str, user_id: int) -> int | None:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT pinned_gift_id FROM economy WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_gift_instance_by_number(db_path: str, collection: str, gift_number: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id, gi.background, gi.gift_number, gi.owner_id, gi.acquired_at,
                      COALESCE(gi.is_admin_gift, 0) AS is_admin_gift,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.model_rarity_pct, gm.tier, gm.custom_emoji_id
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gm.collection = ? AND gi.gift_number = ?""",
            (collection, gift_number)
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
            sql = """SELECT gi.id, gi.background, gi.gift_number,
                            gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                            gm.model_rarity_pct, gm.tier, gm.custom_emoji_id
                     FROM gift_instances gi
                     JOIN gift_models gm ON gm.id = gi.model_id
                     WHERE gi.owner_id IS NULL AND gm.collection = ?
                     ORDER BY gm.model_number, gi.gift_number"""
            params = (collection,)
        else:
            sql = """SELECT gi.id, gi.background, gi.gift_number,
                            gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                            gm.model_rarity_pct, gm.tier, gm.custom_emoji_id
                     FROM gift_instances gi
                     JOIN gift_models gm ON gm.id = gi.model_id
                     WHERE gi.owner_id IS NULL
                     ORDER BY gm.collection, gm.model_number, gi.gift_number"""
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


async def get_all_gift_prices_for_collection(db_path: str, collection: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM gift_prices WHERE collection=?", (collection,)
        ) as cur:
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
            """SELECT go.*, gi.background, gi.gift_number,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji, gm.custom_emoji_id
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
    cutoff = int(time.time()) - 86400
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


async def get_random_bank_gift(db_path: str, tier: str) -> dict | None:
    import random
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gi.id FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.owner_id IS NULL AND gm.tier = ?""",
            (tier,),
        ) as cur:
            ids = [r["id"] async for r in cur]
    if not ids:
        return None
    return await get_gift_instance(db_path, random.choice(ids))


async def toggle_work_reminder(db_path: str, user_id: int) -> int:
    """Flip work_reminder for user. Returns new value (0 or 1)."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "UPDATE economy SET work_reminder = 1 - work_reminder WHERE user_id = ? RETURNING work_reminder",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return row[0] if row else 0


async def get_work_reminder_targets(db_path: str, now: int) -> list[int]:
    """Return user_ids whose cooldown just expired and have reminders enabled."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT user_id FROM economy "
            "WHERE work_reminder = 1 AND last_work > 0 "
            "AND last_work + 900 <= ? AND last_reminder_sent < last_work",
            (now,),
        ) as cur:
            return [row[0] async for row in cur]


async def mark_reminder_sent(db_path: str, user_id: int, now: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE economy SET last_reminder_sent = ? WHERE user_id = ?", (now, user_id)
        )
        await db.commit()


async def get_rob_cooldown(db_path: str, user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT last_rob FROM economy WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def set_rob_cooldown(db_path: str, user_id: int, timestamp: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE economy SET last_rob = ? WHERE user_id = ?", (timestamp, user_id)
        )
        await db.commit()


async def add_heat(
    db_path: str,
    user_id: int,
    amount: int,
    *,
    now: int | None = None,
) -> int:
    """Add decaying Underground Heat and return the capped current value."""
    current_time = int(time.time()) if now is None else now
    async with aiosqlite.connect(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT heat, heat_updated_at FROM economy WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.rollback()
            return 0
        heat = max(0, min(100, int(row[0] or 0)))
        updated_at = int(row[1] or 0)
        if heat and updated_at:
            heat = max(
                0,
                heat - max(0, current_time - updated_at) // (30 * 60),
            )
        heat = min(100, heat + max(0, amount))
        await db.execute(
            "UPDATE economy SET heat = ?, heat_updated_at = ? WHERE user_id = ?",
            (heat, current_time, user_id),
        )
        await db.commit()
        return heat


async def consume_anon_firewall(
    db_path: str,
    user_id: int,
    actor_id: int | None = None,
    *,
    now: int | None = None,
) -> dict:
    """Consume the user's active number firewall if its daily charge is ready."""
    timestamp = int(time.time()) if now is None else int(now)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                """SELECT e.anon_firewall_used_at, a.suffix
                   FROM economy e
                   JOIN anon_numbers a
                     ON a.id = e.pinned_anon_id AND a.owner_id = e.user_id
                   WHERE e.user_id = ?""",
                (user_id,),
            )
        ).fetchone()
        if not row:
            await db.rollback()
            return {"blocked": False, "active": False, "remaining": 0}
        elapsed = timestamp - (row["anon_firewall_used_at"] or 0)
        remaining = max(0, ANON_FIREWALL_COOLDOWN - elapsed)
        if remaining:
            await db.rollback()
            return {
                "blocked": False,
                "active": True,
                "remaining": remaining,
                "suffix": row["suffix"],
            }
        await db.execute(
            "UPDATE economy SET anon_firewall_used_at = ? WHERE user_id = ?",
            (timestamp, user_id),
        )
        await db.execute(
            """INSERT INTO anon_security_events
               (user_id, event_type, detail, amount, actor_id, created_at)
               VALUES (?, 'firewall', 'Blocked an incoming robbery', 0, ?, ?)""",
            (user_id, actor_id, timestamp),
        )
        await db.commit()
        return {
            "blocked": True,
            "active": True,
            "remaining": ANON_FIREWALL_COOLDOWN,
            "suffix": row["suffix"],
        }


async def get_hack_cooldown(db_path: str, user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT last_hack FROM economy WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def set_hack_cooldown(db_path: str, user_id: int, timestamp: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE economy SET last_hack = ? WHERE user_id = ?", (timestamp, user_id)
        )
        await db.commit()


async def get_hack_session(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM hack_sessions WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_hack_session(
    db_path: str, user_id: int, word: str, clue: str,
    reward: int, revealed_indices: str
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR REPLACE INTO hack_sessions
               (user_id, word, clue, reward, attempts, revealed_indices, started_at)
               VALUES (?, ?, ?, ?, 5, ?, ?)""",
            (user_id, word, clue, reward, revealed_indices, now),
        )
        await db.commit()


async def update_hack_session(
    db_path: str, user_id: int, attempts: int, revealed_indices: str
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE hack_sessions SET attempts = ?, revealed_indices = ? WHERE user_id = ?",
            (attempts, revealed_indices, user_id),
        )
        await db.commit()


async def delete_hack_session(db_path: str, user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM hack_sessions WHERE user_id = ?", (user_id,))
        await db.commit()
