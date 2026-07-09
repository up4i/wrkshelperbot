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
    gifts = await get_bank_gifts(db_path)
    assert len(gifts) == 12  # 2 models × 6 backgrounds


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
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO gift_offers (from_user_id, to_user_id, instance_id, wrk_offered, status, created_at) VALUES (?,?,?,?,?,?)",
            (222, 111, bank[0]["id"], 500, "pending", int(time.time()) - 90000)
        )
        await db.commit()
    expired = await expire_old_offers(db_path)
    assert len(expired) == 1
