import asyncio
import sqlite3

import db as database
from miniapp import server


def test_high_balance_hack_reward_respects_cap(tmp_path, monkeypatch):
    path = tmp_path / "hack-high-balance.db"
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO economy (user_id, username, full_name, balance) "
            "VALUES (1, 'highroller', 'High Roller', ?)",
            (100_000_000,),
        )
        conn.commit()

    monkeypatch.setattr(server, "DB_PATH", str(path))
    result = server.hack_start(
        server.HackStartRequest(user_id=1),
        authenticated_user=1,
    )

    assert result["reward"] == 150_000
    assert result["attempts"] == 5


def test_targeted_group_hack_moves_existing_wrk_instead_of_minting(tmp_path, monkeypatch):
    path = tmp_path / "hack-rival.db"
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO economy (user_id, username, full_name, balance) "
            "VALUES (?, ?, ?, ?)",
            [
                (1, "hacker", "Hacker", 1_000),
                (2, "rival", "Rival", 5_000),
            ],
        )
        conn.execute(
            "INSERT INTO hack_sessions "
            "(user_id, word, clue, reward, attempts, revealed_indices, started_at, "
            "target_user_id, target_name, chat_id) "
            "VALUES (1, 'block', 'A chain unit', 900, 5, '0', 1, 2, 'Rival', -1001)"
        )
        conn.execute(
            "INSERT INTO game_token_balances (user_id, symbol, amount) "
            "VALUES (2, 'UTYA', 10000000000)"
        )
        conn.commit()

    monkeypatch.setattr(server, "DB_PATH", str(path))
    monkeypatch.setattr(server, "_send_telegram_dm", lambda *_args, **_kwargs: None)
    result = server.hack_guess(
        server.HackGuessRequest(user_id=1, word="block"),
        authenticated_user=1,
    )

    assert result["result"] == "win"
    assert result["reward"] == 900
    assert result["new_balance"] == 1_900
    assert result["token_loot"]["symbol"] == "UTYA"
    with sqlite3.connect(path) as conn:
        balances = dict(conn.execute("SELECT user_id, balance FROM economy"))
        token_balances = dict(
            conn.execute(
                "SELECT user_id, amount FROM game_token_balances WHERE symbol = 'UTYA'"
            )
        )
    assert balances == {1: 1_900, 2: 4_100}
    assert sum(balances.values()) == 6_000
    assert sum(token_balances.values()) == 10_000_000_000
    assert token_balances[1] == result["token_loot"]["amount"]
