import time
import aiosqlite

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
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT,
    balance     INTEGER NOT NULL DEFAULT 1000,
    streak      INTEGER NOT NULL DEFAULT 0,
    last_daily  INTEGER NOT NULL DEFAULT 0
);
"""

async def _migrate(db) -> None:
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
                   username = excluded.username,
                   full_name = excluded.full_name""",
            (user_id, username, full_name),
        )
        await db.commit()


async def get_wallet(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, full_name, balance, streak, last_daily FROM economy WHERE user_id = ?",
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
                   GROUP BY user_id
               ) a ON a.user_id = e.user_id
               ORDER BY e.balance DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) async for r in cur]


async def claim_daily(db_path: str, user_id: int, amount: int, streak: int, timestamp: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "UPDATE economy SET balance = balance + ?, streak = ?, last_daily = ? WHERE user_id = ? RETURNING balance",
            (amount, streak, timestamp, user_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return row[0] if row else 0
