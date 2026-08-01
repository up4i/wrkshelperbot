import asyncio
import sqlite3
from decimal import Decimal

import db as database
import simulated_market
from game_tokens import parse_token_amount


def _gram_reference():
    return {
        "symbol": "GRAM",
        "name": "Gram",
        "kind": "game_base",
        "price_usd": "2",
        "updated_at": 1_785_566_400,
        "source": "test",
        "source_url": "https://example.com",
        "stale": False,
    }


def test_market_starts_with_established_history_and_full_metrics(tmp_path, monkeypatch):
    path = tmp_path / "market.db"
    asyncio.run(database.init_db(str(path)))
    monkeypatch.setattr(simulated_market, "get_gram_reference", _gram_reference)

    snapshot = simulated_market.get_market_snapshot(
        str(path), now=1_785_566_400
    )
    tokens = {token["symbol"]: token for token in snapshot["tokens"]}

    assert snapshot["market_status"] == "active"
    assert snapshot["market_model"] == "constant_product_amm"
    assert set(tokens) == {
        "GRAM", "UTYA", "REDO", "SCAT", "YODA", "CHERRY",
        "MTONGA", "GROYP", "GRAMMING", "GRM",
    }
    utya = tokens["UTYA"]
    assert utya["age_days"] > 300
    assert utya["holders"] > 10_000
    assert Decimal(utya["market_cap_usd"]) > 0
    assert Decimal(utya["liquidity_gram"]) > 0
    assert Decimal(utya["volume_24h_gram"]) > 0
    assert utya["trades_24h"] > 0
    assert len(utya["sparkline"]) >= 20
    with sqlite3.connect(path) as connection:
        impossible = connection.execute(
            "SELECT symbol FROM simulated_market_pools "
            "WHERE reserve_token <= circulating_supply / 100"
        ).fetchall()
    assert impossible == []


def test_large_buy_moves_pool_price_and_cannot_round_trip_for_profit(tmp_path):
    path = tmp_path / "price-impact.db"
    asyncio.run(database.init_db(str(path)))
    amount_in = parse_token_amount("10000")

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        before = connection.execute(
            "SELECT reserve_gram, reserve_token FROM simulated_market_pools "
            "WHERE symbol = 'UTYA'"
        ).fetchone()
        before_price = Decimal(before["reserve_gram"]) / Decimal(before["reserve_token"])
        buy = simulated_market.execute_market_swap(
            connection, "GRAM", "UTYA", amount_in, user_id=1
        )
        after_buy = connection.execute(
            "SELECT reserve_gram, reserve_token FROM simulated_market_pools "
            "WHERE symbol = 'UTYA'"
        ).fetchone()
        after_price = Decimal(after_buy["reserve_gram"]) / Decimal(after_buy["reserve_token"])
        sell = simulated_market.execute_market_swap(
            connection, "UTYA", "GRAM", buy["output_amount"], user_id=1
        )
        connection.commit()

    assert after_price > before_price
    assert Decimal(buy["price_impact_pct"]) > 10
    assert sell["output_amount"] < amount_in


def test_background_activity_is_persistent_and_rate_limited(tmp_path):
    path = tmp_path / "activity.db"
    asyncio.run(database.init_db(str(path)))

    first = simulated_market.simulate_market_activity(str(path), now=2_000_000_000)
    second = simulated_market.simulate_market_activity(str(path), now=2_000_000_010)

    assert 1 <= first <= 3
    assert second == 0
    with sqlite3.connect(path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM simulated_market_trades "
            "WHERE is_simulated = 1 AND created_at = 2000000000"
        ).fetchone()[0]
    assert count == first
