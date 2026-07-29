import asyncio
import sqlite3

import db as database
from collectibles import (
    ANON_FLOOR_PRICE,
    anon_number_price,
    anon_number_rarity,
    format_anon_number,
)
from miniapp import server


def test_anonymous_number_format_and_price_tiers():
    assert format_anon_number(1) == "+888 001"
    assert format_anon_number(999) == "+888 999"
    assert anon_number_price(124) == ANON_FLOOR_PRICE
    assert anon_number_rarity(121)[0] == "premium"
    assert anon_number_price(123) > ANON_FLOOR_PRICE
    assert anon_number_price(111) > anon_number_price(123)


def _seed_shop_db(path):
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO economy (user_id, username, full_name, balance, pinned_gift_id) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (1, "seller", "Seller", 200_000_000, 101),
                (2, "buyer", "Buyer", 200_000_000, None),
            ],
        )
        conn.execute(
            "INSERT INTO gift_models "
            "(id, collection, model_number, model_name, model_emoji, model_rarity_pct, tier) "
            "VALUES (1, 'plush_pepe', 1, 'Plush Pepe', '🐸', 1.0, 'high')"
        )
        conn.executemany(
            "INSERT INTO gift_instances "
            "(id, model_id, background, gift_number, owner_id, acquired_at) "
            "VALUES (?, 1, ?, ?, 1, 1)",
            [(101, "black", 7), (102, "onyx", 8)],
        )
        conn.executemany(
            "INSERT INTO gift_prices "
            "(collection, background, base_price, current_price, demand_pressure, last_updated) "
            "VALUES ('plush_pepe', ?, 10000000, ?, 0, 1)",
            [("black", 30_000_000), ("onyx", 20_000_000)],
        )
        conn.commit()


def test_mkrt_rift_wallet_and_anon_ownership(tmp_path, monkeypatch):
    path = tmp_path / "shop.db"
    _seed_shop_db(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))

    listing = server.mkrt_create_listing(
        server.MkrtListRequest(user_id=1, gift_id=101, price=45_000_000),
        authenticated_user=1,
    )
    wallet = server.shop_wallet(user_id=1, authenticated_user=1)
    listed_gift = next(g for g in wallet["gifts"] if g["id"] == 101)
    assert listed_gift["listing_id"] == listing["listing_id"]
    assert listed_gift["buyback_price"] == 24_000_000

    purchase = server.mkrt_buy_listing(
        listing["listing_id"],
        server.ShopActorRequest(user_id=2),
        authenticated_user=2,
    )
    assert purchase["new_balance"] == 155_000_000

    sale = server.rift_sell(
        server.RiftSellRequest(user_id=1, gift_id=102),
        authenticated_user=1,
    )
    assert sale["buyback_price"] == 16_000_000

    anon_purchase = server.fragsmint_buy(
        server.AnonBuyRequest(user_id=2, anon_id=124),
        authenticated_user=2,
    )
    assert anon_purchase["number"] == "+888 124"
    server.pin_anon_number(
        server.AnonPinRequest(user_id=2, anon_id=124),
        authenticated_user=2,
    )

    buyer_wallet = server.shop_wallet(user_id=2, authenticated_user=2)
    assert buyer_wallet["anon_numbers"][0]["number"] == "+888 124"
    assert buyer_wallet["anon_numbers"][0]["is_pinned"] is True

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        profile = server._load_profile(conn, 2)
    assert profile["pinned_anon"]["number"] == "+888 124"
    assert profile["anon_value"] == ANON_FLOOR_PRICE

    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT owner_id FROM gift_instances WHERE id = 101"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT owner_id FROM gift_instances WHERE id = 102"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT pinned_gift_id FROM economy WHERE user_id = 1"
        ).fetchone()[0] is None
