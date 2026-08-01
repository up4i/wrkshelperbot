import aiosqlite
import pytest

from db import (
    get_hack_session,
    get_user_badges,
    get_user_by_username,
    init_db,
    raid_game_token,
    save_hack_session,
    transfer_balance_up_to,
    update_activity,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_init_creates_only_game_and_player_tracking_tables(db_path):
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = {row[0] async for row in cur}

    assert {
        "economy",
        "game_stats",
        "user_activity",
        "gift_instances",
        "game_wallets",
        "game_token_balances",
        "game_token_transactions",
        "simulated_market_pools",
        "simulated_market_trades",
        "simulated_market_candles",
    } <= tables
    assert {
        "groups",
        "warnings",
        "punishments",
        "halo_users",
        "autoreplies",
        "blocklist",
    }.isdisjoint(tables)


@pytest.mark.asyncio
async def test_group_activity_supports_chat_player_lookup(db_path):
    await init_db(db_path)
    await update_activity(db_path, -100111, 42, "rival", "Rival Player")

    player = await get_user_by_username(db_path, -100111, "@RIVAL")

    assert player == {"user_id": 42, "full_name": "Rival Player"}


@pytest.mark.asyncio
async def test_atomic_capped_wallet_transfer(db_path):
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as connection:
        await connection.executemany(
            "INSERT INTO economy (user_id, balance) VALUES (?, ?)",
            [(1, 750), (2, 100)],
        )
        await connection.commit()

    result = await transfer_balance_up_to(db_path, 1, 2, 1_000)

    assert result == {"amount": 750, "from_balance": 0, "to_balance": 850}


@pytest.mark.asyncio
async def test_targeted_hack_session_metadata_is_persisted(db_path):
    await init_db(db_path)
    await save_hack_session(
        db_path,
        1,
        "block",
        "A chain unit",
        900,
        "0",
        target_user_id=2,
        target_name="Rival",
        chat_id=-100123,
    )

    session = await get_hack_session(db_path, 1)
    assert session["target_user_id"] == 2
    assert session["target_name"] == "Rival"
    assert session["chat_id"] == -100123


@pytest.mark.asyncio
async def test_game_token_raid_moves_only_fictional_wallet_balance(db_path):
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as connection:
        await connection.executemany(
            "INSERT INTO economy (user_id, balance) VALUES (?, ?)",
            [(1, 1_000), (2, 1_000)],
        )
        await connection.execute(
            "INSERT INTO game_token_balances (user_id, symbol, amount) VALUES (1, 'GRAM', ?)",
            (10_000_000_000,),
        )
        await connection.commit()

    loot = await raid_game_token(db_path, 1, 2, 1_000, "rob")

    assert loot == {
        "symbol": "GRAM",
        "amount": 1_000_000_000,
        "display_amount": "1",
    }
    async with aiosqlite.connect(db_path) as connection:
        victim = await (
            await connection.execute(
                "SELECT amount FROM game_token_balances WHERE user_id = 1 AND symbol = 'GRAM'"
            )
        ).fetchone()
        attacker = await (
            await connection.execute(
                "SELECT amount FROM game_token_balances WHERE user_id = 2 AND symbol = 'GRAM'"
            )
        ).fetchone()
        wrk = await (
            await connection.execute(
                "SELECT user_id, balance FROM economy ORDER BY user_id"
            )
        ).fetchall()
    assert victim[0] == 9_000_000_000
    assert attacker[0] == 1_000_000_000
    assert wrk == [(1, 1_000), (2, 1_000)]


@pytest.mark.asyncio
async def test_whale_badge_requires_strictly_more_than_one_percent(db_path):
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as connection:
        supply = (
            await (
                await connection.execute(
                    "SELECT circulating_supply FROM simulated_market_pools "
                    "WHERE symbol = 'UTYA'"
                )
            ).fetchone()
        )[0]
        await connection.execute(
            "INSERT INTO economy (user_id, balance) VALUES (1, 1000)"
        )
        await connection.execute(
            "INSERT INTO game_token_balances (user_id, symbol, amount) "
            "VALUES (1, 'UTYA', ?)",
            (supply // 100,),
        )
        await connection.commit()

    assert "whale:UTYA" not in await get_user_badges(db_path, 1, owner_id=999)

    async with aiosqlite.connect(db_path) as connection:
        await connection.execute(
            "UPDATE game_token_balances SET amount = amount + 1 "
            "WHERE user_id = 1 AND symbol = 'UTYA'"
        )
        await connection.commit()

    assert "whale:UTYA" in await get_user_badges(db_path, 1, owner_id=999)
