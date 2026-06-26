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
"""

async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()

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
