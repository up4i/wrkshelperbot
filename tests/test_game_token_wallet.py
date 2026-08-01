import asyncio
import sqlite3

import pytest

import db as database
from miniapp import server


def _market_snapshot(_db_path=None):
    return {
        "updated_at": 1_785_566_400,
        "errors": {},
        "tokens": [
            {
                "symbol": "GRAM",
                "name": "Gram",
                "kind": "game_base",
                "price_usd": "2",
                "price_gram": "1",
                "updated_at": 1_785_566_400,
                "source": "test",
                "source_url": "https://example.com/gram",
                "stale": False,
            },
            {
                "symbol": "UTYA",
                "name": "Utya",
                "kind": "game_token",
                "price_usd": "0.04",
                "price_gram": "0.02",
                "updated_at": 1_785_566_400,
                "source": "test",
                "source_url": "https://example.com/utya",
                "stale": False,
            },
        ],
    }


@pytest.fixture
def wallet_db(tmp_path, monkeypatch):
    path = tmp_path / "wallet.db"
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO economy (user_id, username, full_name, balance) VALUES (?, ?, ?, ?)",
            [
                (1, "alice", "Alice", 30_000_000),
                (2, "bob", "Bob", 30_000_000),
            ],
        )
    monkeypatch.setattr(server, "DB_PATH", str(path))
    monkeypatch.setattr(server, "get_market_snapshot", _market_snapshot)
    monkeypatch.setattr(server.config, "WRK_PER_USD", 1_000_000)
    monkeypatch.setattr(server.config, "CUSTOM_ADDRESS_GRAM_PRICE", "5")
    return path


def test_fragsmint_swap_and_cashout_complete_the_simulated_loop(wallet_db):
    topup = server.game_wallet_topup(
        server.GameTokenAmountRequest(user_id=1, amount="10"),
        authenticated_user=1,
    )
    assert topup["wrk_spent"] == 20_000_000
    assert topup["new_gram_balance"] == "10"

    swap = server.game_wallet_swap(
        server.GameTokenSwapRequest(
            user_id=1,
            from_symbol="GRAM",
            to_symbol="UTYA",
            amount="2",
        ),
        authenticated_user=1,
    )
    received_utya = float(swap["amount_received"])
    assert 0 < received_utya < 2 / 0.02827
    assert float(swap["price_impact_pct"]) > 0.3

    reverse = server.game_wallet_swap(
        server.GameTokenSwapRequest(
            user_id=1,
            from_symbol="UTYA",
            to_symbol="GRAM",
            amount=swap["amount_received"],
        ),
        authenticated_user=1,
    )
    assert 0 < float(reverse["amount_received"]) < 2

    cashout = server.game_wallet_cashout(
        server.GameTokenAmountRequest(user_id=1, amount="1.5"),
        authenticated_user=1,
    )
    assert cashout["wrk_received"] == 3_000_000
    assert cashout["new_wrk_balance"] == 13_000_000

    wallet = server.game_wallet(user_id=1, authenticated_user=1)
    holdings = {item["symbol"]: item["display_amount"] for item in wallet["holdings"]}
    assert float(holdings["GRAM"]) > 6.4
    assert holdings["UTYA"] == "0"
    assert {item["transaction_type"] for item in wallet["transactions"]} == {
        "fragsmint_topup",
        "stonk_swap",
        "gram_cashout",
    }


def test_swap_quote_matches_execution_and_does_not_move_pool(wallet_db):
    server.game_wallet_topup(
        server.GameTokenAmountRequest(user_id=1, amount="5"),
        authenticated_user=1,
    )
    request = server.GameTokenSwapRequest(
        user_id=1,
        from_symbol="GRAM",
        to_symbol="UTYA",
        amount="3",
    )
    quote = server.game_wallet_swap_quote(request, authenticated_user=1)
    second_quote = server.game_wallet_swap_quote(request, authenticated_user=1)
    assert quote == second_quote

    execution = server.game_wallet_swap(request, authenticated_user=1)
    assert execution["amount_received"] == quote["amount_received"]
    assert execution["price_impact_pct"] == quote["price_impact_pct"]


def test_custom_game_address_and_internal_token_send(wallet_db):
    server.game_wallet_topup(
        server.GameTokenAmountRequest(user_id=1, amount="6"),
        authenticated_user=1,
    )
    custom = server.game_wallet_custom_address(
        server.CustomWalletAddressRequest(user_id=1, address="alice-vault"),
        authenticated_user=1,
    )
    assert custom == {"custom_address": "alice-vault.wrk", "gram_spent": "5"}

    server.game_wallet_topup(
        server.GameTokenAmountRequest(user_id=2, amount="2"),
        authenticated_user=2,
    )
    sent = server.game_wallet_send(
        server.GameTokenSendRequest(
            user_id=2,
            recipient="alice-vault.wrk",
            symbol="GRAM",
            amount="1.25",
        ),
        authenticated_user=2,
    )
    assert sent["amount"] == "1.25"

    alice = server.game_wallet(user_id=1, authenticated_user=1)
    bob = server.game_wallet(user_id=2, authenticated_user=2)
    assert alice["payment_address"] == "alice-vault.wrk"
    assert alice["holdings"][0]["display_amount"] == "2.25"
    assert bob["holdings"][0]["display_amount"] == "0.75"


def test_successful_miniapp_robbery_can_take_simulated_tokens(wallet_db, monkeypatch):
    server.game_wallet_topup(
        server.GameTokenAmountRequest(user_id=2, amount="10"),
        authenticated_user=2,
    )
    monkeypatch.setattr(server.random, "random", lambda: 0.0)
    monkeypatch.setattr(server.random, "uniform", lambda _low, _high: 0.10)
    monkeypatch.setattr(server.random, "randint", lambda low, _high: low)
    monkeypatch.setattr(server.random, "choice", lambda choices: choices[0])
    monkeypatch.setattr(server, "_send_telegram_dm", lambda *_args, **_kwargs: None)

    result = server.rob_attempt(
        server.RobAttemptRequest(user_id=1, target_id=2),
        authenticated_user=1,
    )

    assert result["outcome"] == "success"
    assert result["token_loot"] == {
        "symbol": "GRAM",
        "amount": 100_000_000,
        "display_amount": "0.1",
    }
    alice = server.game_wallet(user_id=1, authenticated_user=1)
    bob = server.game_wallet(user_id=2, authenticated_user=2)
    assert alice["holdings"][0]["display_amount"] == "0.1"
    assert bob["holdings"][0]["display_amount"] == "9.9"


def test_miniapp_profile_badges_include_qualified_token_whales(wallet_db):
    with sqlite3.connect(wallet_db) as connection:
        supply = connection.execute(
            "SELECT circulating_supply FROM simulated_market_pools WHERE symbol = 'UTYA'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO game_token_balances (user_id, symbol, amount) VALUES (1, 'UTYA', ?)",
            (supply // 100 + 1,),
        )

    badges = server.get_user_badges(1)["badges"]

    assert "whale:UTYA" in badges
