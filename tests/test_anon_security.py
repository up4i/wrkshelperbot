import asyncio
import sqlite3
import time

import pytest

import db as database
from collectibles import ANON_FIREWALL_COOLDOWN
from miniapp import server


def _seed_security_db(path):
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO economy (user_id,username,full_name,balance) "
            "VALUES (?,?,?,?)",
            [
                (1, "alice", "Alice", 200_000_000),
                (2, "bob", "Bob", 150_000_000),
            ],
        )
        conn.execute(
            "UPDATE anon_numbers SET owner_id=1, acquired_at=1 WHERE id IN (124,126)"
        )
        conn.execute(
            "UPDATE anon_numbers SET owner_id=2, acquired_at=1 WHERE id=125"
        )
        conn.commit()


def _activate(user_id, anon_id):
    return server.pin_anon_number(
        server.AnonPinRequest(user_id=user_id, anon_id=anon_id),
        authenticated_user=user_id,
    )


def test_secure_vault_lifecycle_and_active_number_lock(tmp_path, monkeypatch):
    path = tmp_path / "security.db"
    _seed_security_db(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    _activate(1, 124)

    masked = server.security_mask(
        server.SecurityMaskRequest(user_id=1, enabled=True),
        authenticated_user=1,
    )
    assert masked["active_number"] == "+888 124"
    assert masked["mask_enabled"] is True

    deposited = server.security_vault_deposit(
        server.SecurityAmountRequest(user_id=1, amount=1_000_000),
        authenticated_user=1,
    )
    assert deposited["balance"] == 199_000_000
    assert deposited["vault_balance"] == 1_000_000

    with pytest.raises(Exception, match="secures vault funds"):
        server.create_trade(
            server.TradeCreateRequest(
                from_user_id=1,
                to_user_id=2,
                offer_anon_id=124,
            ),
            authenticated_user=1,
        )
    with pytest.raises(Exception, match="Move all funds out"):
        server.pin_anon_number(
            server.AnonPinRequest(user_id=1, anon_id=None),
            authenticated_user=1,
        )

    pending = server.security_vault_withdraw(
        server.SecurityAmountRequest(user_id=1, amount=400_000),
        authenticated_user=1,
    )
    assert pending["vault_balance"] == 600_000
    assert pending["pending_amount"] == 400_000
    assert pending["withdraw_ready"] is False

    cancelled = server.security_vault_cancel(
        server.SecurityActorRequest(user_id=1),
        authenticated_user=1,
    )
    assert cancelled["vault_balance"] == 1_000_000
    assert cancelled["pending_amount"] == 0

    server.security_vault_withdraw(
        server.SecurityAmountRequest(user_id=1, amount=400_000),
        authenticated_user=1,
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE economy SET vault_withdraw_available_at=? WHERE user_id=1",
            (int(time.time()) - 1,),
        )
        conn.commit()
    claimed = server.security_vault_claim(
        server.SecurityActorRequest(user_id=1),
        authenticated_user=1,
    )
    assert claimed["balance"] == 199_400_000
    assert claimed["vault_balance"] == 600_000
    assert claimed["pending_amount"] == 0

    # Vault security follows the active number, not every number in the wallet.
    _activate(1, 126)
    trade = server.create_trade(
        server.TradeCreateRequest(
            from_user_id=1,
            to_user_id=2,
            offer_anon_id=124,
        ),
        authenticated_user=1,
    )
    assert trade["ok"] is True


def test_identity_mask_and_daily_robbery_firewall(tmp_path, monkeypatch):
    path = tmp_path / "firewall.db"
    _seed_security_db(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    monkeypatch.setattr(server, "_send_telegram_dm", lambda *_args, **_kwargs: None)
    _activate(2, 125)
    server.security_mask(
        server.SecurityMaskRequest(user_id=2, enabled=True),
        authenticated_user=2,
    )

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        profile = server._load_profile(conn, 2)
    assert profile["name"] == "+888 125"
    assert profile["identity_masked"] is True
    assert profile["username"] is None

    leaderboard = server.leaderboard(tab="balance", limit=10)
    bob = next(row for row in leaderboard if row["user_id"] == 2)
    assert bob["name"] == "+888 125"
    assert bob["identity_masked"] is True

    before = {}
    with sqlite3.connect(path) as conn:
        before["alice"] = conn.execute(
            "SELECT balance FROM economy WHERE user_id=1"
        ).fetchone()[0]
        before["bob"] = conn.execute(
            "SELECT balance FROM economy WHERE user_id=2"
        ).fetchone()[0]

    blocked = server.rob_attempt(
        server.RobAttemptRequest(user_id=1, target_id=2),
        authenticated_user=1,
    )
    assert blocked["outcome"] == "firewall"
    assert blocked["amount"] == 0

    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=1"
        ).fetchone()[0] == before["alice"]
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=2"
        ).fetchone()[0] == before["bob"]
        assert conn.execute(
            "SELECT COUNT(*) FROM anon_security_events "
            "WHERE user_id=2 AND event_type='firewall'"
        ).fetchone()[0] == 1
        conn.execute("UPDATE economy SET last_rob=0 WHERE user_id=1")
        conn.commit()

    # The next attack inside 24 hours is resolved normally; numbers do not stack.
    second = server.rob_attempt(
        server.RobAttemptRequest(user_id=1, target_id=2),
        authenticated_user=1,
    )
    assert second["outcome"] != "firewall"

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE economy SET anon_firewall_used_at=? WHERE user_id=2",
            (int(time.time()) - ANON_FIREWALL_COOLDOWN - 1,),
        )
        conn.execute("UPDATE economy SET last_rob=0 WHERE user_id=1")
        conn.commit()
    recharged = server.rob_attempt(
        server.RobAttemptRequest(user_id=1, target_id=2),
        authenticated_user=1,
    )
    assert recharged["outcome"] == "firewall"


def test_async_bot_firewall_helper_uses_same_daily_charge(tmp_path):
    path = tmp_path / "bot-firewall.db"
    _seed_security_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE economy SET pinned_anon_id=125 WHERE user_id=2")
        conn.commit()

    first = asyncio.run(
        database.consume_anon_firewall(str(path), 2, actor_id=1, now=1_000_000)
    )
    second = asyncio.run(
        database.consume_anon_firewall(str(path), 2, actor_id=1, now=1_000_001)
    )
    assert first["blocked"] is True
    assert first["suffix"] == 125
    assert second["blocked"] is False
    assert second["remaining"] == ANON_FIREWALL_COOLDOWN - 1
