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
