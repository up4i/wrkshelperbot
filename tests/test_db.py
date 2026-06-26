import time
import pytest
import aiosqlite
from db import init_db, upsert_group, get_group, update_group

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")

@pytest.mark.asyncio
async def test_init_creates_tables(db_path):
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = {row[0] async for row in cur}
    assert {"groups", "warnings", "punishments"}.issubset(tables)

@pytest.mark.asyncio
async def test_upsert_group_creates_defaults(db_path):
    await init_db(db_path)
    await upsert_group(db_path, -100111)
    row = await get_group(db_path, -100111)
    assert row["chat_id"] == -100111
    assert row["warn_limit"] == 3
    assert row["warn_action"] == "mute"
    assert row["log_channel_id"] is None

@pytest.mark.asyncio
async def test_upsert_group_idempotent(db_path):
    await init_db(db_path)
    await upsert_group(db_path, -100111)
    await upsert_group(db_path, -100111)
    row = await get_group(db_path, -100111)
    assert row is not None

@pytest.mark.asyncio
async def test_update_group(db_path):
    await init_db(db_path)
    await upsert_group(db_path, -100111)
    await update_group(db_path, -100111, warn_limit=5, warn_action="ban")
    row = await get_group(db_path, -100111)
    assert row["warn_limit"] == 5
    assert row["warn_action"] == "ban"

from db import add_warning, get_warnings, reset_warnings, add_punishment, remove_punishment, get_expired_punishments, delete_punishment_by_id

@pytest.mark.asyncio
async def test_add_warning_increments(db_path):
    await init_db(db_path)
    count = await add_warning(db_path, -100111, 999, "spam")
    assert count == 1
    count = await add_warning(db_path, -100111, 999, "again")
    assert count == 2

@pytest.mark.asyncio
async def test_get_warnings_returns_last_reason(db_path):
    await init_db(db_path)
    await add_warning(db_path, -100111, 999, "first")
    await add_warning(db_path, -100111, 999, "second")
    row = await get_warnings(db_path, -100111, 999)
    assert row["count"] == 2
    assert row["last_reason"] == "second"

@pytest.mark.asyncio
async def test_get_warnings_none_for_new_user(db_path):
    await init_db(db_path)
    row = await get_warnings(db_path, -100111, 888)
    assert row is None

@pytest.mark.asyncio
async def test_reset_warnings(db_path):
    await init_db(db_path)
    await add_warning(db_path, -100111, 999, "test")
    await reset_warnings(db_path, -100111, 999)
    row = await get_warnings(db_path, -100111, 999)
    assert row is None

@pytest.mark.asyncio
async def test_add_and_get_expired_punishments(db_path):
    await init_db(db_path)
    past = int(time.time()) - 10
    await add_punishment(db_path, -100111, 999, "mute", past)
    expired = await get_expired_punishments(db_path)
    assert len(expired) == 1
    assert expired[0]["user_id"] == 999

@pytest.mark.asyncio
async def test_future_punishment_not_expired(db_path):
    await init_db(db_path)
    future = int(time.time()) + 9999
    await add_punishment(db_path, -100111, 999, "mute", future)
    expired = await get_expired_punishments(db_path)
    assert len(expired) == 0

@pytest.mark.asyncio
async def test_delete_punishment_by_id(db_path):
    await init_db(db_path)
    past = int(time.time()) - 10
    await add_punishment(db_path, -100111, 999, "mute", past)
    expired = await get_expired_punishments(db_path)
    await delete_punishment_by_id(db_path, expired[0]["id"])
    assert await get_expired_punishments(db_path) == []

@pytest.mark.asyncio
async def test_remove_punishment(db_path):
    await init_db(db_path)
    await add_punishment(db_path, -100111, 999, "ban", None)
    await remove_punishment(db_path, -100111, 999, "ban")
    assert await get_expired_punishments(db_path) == []
