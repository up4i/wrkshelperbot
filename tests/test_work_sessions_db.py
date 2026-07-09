import time
import pytest
from db import init_db, upsert_wallet, get_work_session, start_work_session, sync_work_session, end_work_session

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")

@pytest.mark.asyncio
async def test_get_work_session_none_when_no_session(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    result = await get_work_session(db_path, 1)
    assert result is None

@pytest.mark.asyncio
async def test_start_work_session_creates_row(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    session = await start_work_session(db_path, 1, tap_count_start=50, job_tier_index=1)
    assert session["user_id"] == 1
    assert session["taps"] == 0
    assert session["earned"] == 0
    assert session["job_tier_index"] == 1
    assert session["tap_count_start"] == 50

@pytest.mark.asyncio
async def test_get_work_session_returns_row_after_start(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    session = await get_work_session(db_path, 1)
    assert session is not None
    assert session["user_id"] == 1

@pytest.mark.asyncio
async def test_sync_work_session_accumulates(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    updated = await sync_work_session(db_path, 1, taps_delta=5, earned_delta=450)
    assert updated["taps"] == 5
    assert updated["earned"] == 450
    updated2 = await sync_work_session(db_path, 1, taps_delta=3, earned_delta=270)
    assert updated2["taps"] == 8
    assert updated2["earned"] == 720

@pytest.mark.asyncio
async def test_sync_work_session_returns_none_for_no_session(db_path):
    await init_db(db_path)
    result = await sync_work_session(db_path, 99, taps_delta=1, earned_delta=100)
    assert result is None

@pytest.mark.asyncio
async def test_end_work_session_returns_final_state_and_deletes(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    await sync_work_session(db_path, 1, taps_delta=10, earned_delta=900)
    final = await end_work_session(db_path, 1)
    assert final["taps"] == 10
    assert final["earned"] == 900
    assert await get_work_session(db_path, 1) is None

@pytest.mark.asyncio
async def test_end_work_session_returns_none_when_no_session(db_path):
    await init_db(db_path)
    result = await end_work_session(db_path, 99)
    assert result is None

@pytest.mark.asyncio
async def test_start_work_session_replaces_existing(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    await sync_work_session(db_path, 1, taps_delta=10, earned_delta=900)
    session2 = await start_work_session(db_path, 1, tap_count_start=100, job_tier_index=1)
    assert session2["taps"] == 0
    assert session2["earned"] == 0
    assert session2["job_tier_index"] == 1
