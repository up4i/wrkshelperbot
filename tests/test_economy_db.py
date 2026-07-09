import time
import pytest
from db import init_db, upsert_wallet, get_wallet, update_balance, get_leaderboard, claim_daily

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")

@pytest.mark.asyncio
async def test_upsert_wallet_creates_with_1000(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    w = await get_wallet(db_path, 111)
    assert w["balance"] == 1000
    assert w["streak"] == 0

@pytest.mark.asyncio
async def test_upsert_wallet_idempotent(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    await update_balance(db_path, 111, 500)
    await upsert_wallet(db_path, 111, "alice_new", "Alice New")
    w = await get_wallet(db_path, 111)
    assert w["balance"] == 1500
    assert w["full_name"] == "Alice New"

@pytest.mark.asyncio
async def test_update_balance_add(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    new_bal = await update_balance(db_path, 111, 200)
    assert new_bal == 1200

@pytest.mark.asyncio
async def test_update_balance_subtract(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    new_bal = await update_balance(db_path, 111, -500)
    assert new_bal == 500

@pytest.mark.asyncio
async def test_get_wallet_none_for_unknown(db_path):
    await init_db(db_path)
    w = await get_wallet(db_path, 999)
    assert w is None

@pytest.mark.asyncio
async def test_leaderboard_order(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "a", "A")
    await upsert_wallet(db_path, 2, "b", "B")
    await upsert_wallet(db_path, 3, "c", "C")
    await update_balance(db_path, 1, 5000)
    await update_balance(db_path, 3, 2000)
    rows = await get_leaderboard(db_path, limit=10)
    assert rows[0]["user_id"] == 1
    assert rows[1]["user_id"] == 3

@pytest.mark.asyncio
async def test_claim_daily_updates_streak_and_balance(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    now = int(time.time())
    new_bal = await claim_daily(db_path, 111, amount=1000, streak=3, timestamp=now)
    assert new_bal == 2000
    w = await get_wallet(db_path, 111)
    assert w["streak"] == 3
    assert w["last_daily"] == now

@pytest.mark.asyncio
async def test_update_balance_no_negative(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    new_bal = await update_balance(db_path, 111, -9999)
    assert new_bal == 0  # floored at 0, not negative
