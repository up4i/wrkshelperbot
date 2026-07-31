import asyncio
import sqlite3

import pytest

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


def test_anon_trade_is_atomic_and_clears_pins(tmp_path, monkeypatch):
    path = tmp_path / "trade.db"
    _seed_shop_db(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE anon_numbers SET owner_id=1, acquired_at=1 WHERE id=124"
        )
        conn.execute(
            "UPDATE anon_numbers SET owner_id=2, acquired_at=1 WHERE id=125"
        )
        conn.execute(
            "UPDATE economy SET pinned_anon_id=124 WHERE user_id=1"
        )
        conn.execute(
            "UPDATE economy SET pinned_anon_id=125 WHERE user_id=2"
        )
        conn.commit()

    first = server.create_trade(
        server.TradeCreateRequest(
            from_user_id=1,
            to_user_id=2,
            offer_anon_id=124,
            offer_wrk=1_000,
            request_anon_id=125,
            request_wrk=2_000,
        ),
        authenticated_user=1,
    )
    assert first["ok"] is True
    # A second offer can be composed, but accepting the first one invalidates
    # every other pending offer that references either transferred number.
    server.create_trade(
        server.TradeCreateRequest(
            from_user_id=1,
            to_user_id=2,
            offer_anon_id=124,
        ),
        authenticated_user=1,
    )

    trades = server.get_trades(user_id=2, authenticated_user=2)
    assert trades["incoming"][0]["offer_anon"]["number"] == "+888 124"
    assert trades["incoming"][0]["request_anon"]["number"] == "+888 125"
    accepted_id = trades["incoming"][0]["id"]
    server.accept_trade(
        accepted_id,
        server.TradeActionRequest(user_id=2),
        authenticated_user=2,
    )

    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT owner_id FROM anon_numbers WHERE id=124"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT owner_id FROM anon_numbers WHERE id=125"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT pinned_anon_id FROM economy WHERE user_id=1"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT pinned_anon_id FROM economy WHERE user_id=2"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=1"
        ).fetchone()[0] == 200_001_000
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=2"
        ).fetchone()[0] == 199_999_000
        statuses = [
            row[0]
            for row in conn.execute(
                "SELECT status FROM gift_offers ORDER BY id"
            ).fetchall()
        ]
        assert statuses == ["accepted", "rejected"]


def test_admin_gifts_are_locked_from_trade_and_shop(tmp_path, monkeypatch):
    path = tmp_path / "admin-gift.db"
    _seed_shop_db(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE gift_instances SET is_admin_gift=1 WHERE id=101"
        )
        conn.execute(
            "UPDATE economy SET pinned_gift_id=NULL WHERE user_id=1"
        )
        conn.commit()

    server.pin_gift(
        server.PinGiftRequest(user_id=1, gift_id=101),
        authenticated_user=1,
    )
    wallet = server.shop_wallet(user_id=1, authenticated_user=1)
    admin_gift = next(gift for gift in wallet["gifts"] if gift["id"] == 101)
    assert admin_gift["is_admin_gift"] == 1
    assert admin_gift["is_pinned"] is True

    with pytest.raises(Exception, match="Admin gifts cannot be traded"):
        server.create_trade(
            server.TradeCreateRequest(
                from_user_id=1,
                to_user_id=2,
                offer_gift_id=101,
            ),
            authenticated_user=1,
        )
    with pytest.raises(Exception, match="Admin gifts cannot be sold"):
        server.rift_sell(
            server.RiftSellRequest(user_id=1, gift_id=101),
            authenticated_user=1,
        )
    with pytest.raises(Exception, match="Admin gifts cannot be listed"):
        server.mkrt_create_listing(
            server.MkrtListRequest(user_id=1, gift_id=101, price=1_000),
            authenticated_user=1,
        )
    with sqlite3.connect(path) as conn:
        listing_id = conn.execute(
            "INSERT INTO gift_market_listings "
            "(gift_id,seller_id,price,status,created_at) "
            "VALUES (101,1,1000,'active',1)"
        ).lastrowid
        conn.commit()
    with pytest.raises(Exception, match="Admin gifts cannot be traded"):
        server.mkrt_buy_listing(
            listing_id,
            server.ShopActorRequest(user_id=2),
            authenticated_user=2,
        )

    # Acceptance revalidates legacy/crafted rows instead of trusting creation.
    with sqlite3.connect(path) as conn:
        offer_id = conn.execute(
            "INSERT INTO gift_offers "
            "(from_user_id,to_user_id,instance_id,status,created_at) "
            "VALUES (1,2,101,'pending',1)"
        ).lastrowid
        conn.commit()
    with pytest.raises(Exception, match="Admin gifts cannot be traded"):
        server.accept_trade(
            offer_id,
            server.TradeActionRequest(user_id=2),
            authenticated_user=2,
        )
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT owner_id FROM gift_instances WHERE id=101"
        ).fetchone()[0] == 1


def test_trade_migration_makes_offered_gift_optional(tmp_path):
    path = tmp_path / "legacy-trades.db"
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE gift_offers")
        conn.execute(
            """CREATE TABLE gift_offers (
                id INTEGER PRIMARY KEY,
                from_user_id INTEGER NOT NULL,
                to_user_id INTEGER NOT NULL,
                instance_id INTEGER NOT NULL REFERENCES gift_instances(id),
                wrk_offered INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )"""
        )
        conn.commit()

    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        info = {
            row[1]: row
            for row in conn.execute("PRAGMA table_info(gift_offers)").fetchall()
        }
        assert info["instance_id"][3] == 0
        assert "offer_anon_id" in info
        conn.execute(
            "INSERT INTO gift_offers "
            "(from_user_id,to_user_id,offer_anon_id,status,created_at) "
            "VALUES (1,2,124,'pending',1)"
        )


def test_large_profile_gift_pages_include_all_450_items(tmp_path, monkeypatch):
    path = tmp_path / "large-profile.db"
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO economy (user_id,username,full_name,balance) "
            "VALUES (99,'nic','Nic',1000)"
        )
        conn.executemany(
            "INSERT INTO gift_models "
            "(id,collection,model_number,model_name,model_emoji,"
            "model_rarity_pct,tier) VALUES (?,'plush_pepe',?,?,'🐸',1.0,'high')",
            [
                (number, number, f"Pepe {number}")
                for number in range(1, 451)
            ],
        )
        conn.executemany(
            "INSERT INTO gift_instances "
            "(id,model_id,background,gift_number,owner_id,acquired_at,is_admin_gift) "
            "VALUES (?,?, 'black',?,99,?,?)",
            [
                (
                    10_000 + number,
                    number,
                    number,
                    1_000 + number,
                    1 if number == 449 else 0,
                )
                for number in range(1, 451)
            ],
        )
        conn.execute(
            "INSERT INTO gift_prices "
            "(collection,background,base_price,current_price,demand_pressure,last_updated) "
            "VALUES ('plush_pepe','black',100,100,0,1)"
        )
        conn.commit()
    monkeypatch.setattr(server, "DB_PATH", str(path))

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        initial = server._load_profile(conn, 99)
    assert initial["gift_count"] == 450
    assert initial["admin_gift_count"] == 1
    assert len(initial["gifts"]) == 20
    assert initial["has_more"] is True

    offset = 0
    all_gifts = []
    while True:
        page = server.profile_gifts_page(99, offset=offset, limit=20)
        all_gifts.extend(page["gifts"])
        offset = page["next_offset"]
        if not page["has_more"]:
            break

    assert len(all_gifts) == 450
    assert len({gift["id"] for gift in all_gifts}) == 450
    admin_gift = next(gift for gift in all_gifts if gift["gift_number"] == 449)
    assert admin_gift["is_admin_gift"] == 1
    assert offset == 450


def test_new_profile_gifts_are_appended_after_existing_gifts(tmp_path):
    path = tmp_path / "profile-gift-order.db"
    _seed_shop_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE gift_instances SET acquired_at = 10 WHERE id = 101"
        )
        conn.execute(
            "UPDATE gift_instances SET acquired_at = 20 WHERE id = 102"
        )
        conn.execute(
            "INSERT INTO gift_instances "
            "(id, model_id, background, gift_number, owner_id, acquired_at) "
            "VALUES (103, 1, 'grape', 9, 1, 30)"
        )
        conn.execute(
            "INSERT INTO gift_prices "
            "(collection, background, base_price, current_price, "
            "demand_pressure, last_updated) "
            "VALUES ('plush_pepe', 'grape', 10000000, 10000000, 0, 1)"
        )
        conn.commit()
        conn.row_factory = sqlite3.Row

        gifts = server._profile_gift_page(conn, 1, 0, 20)

    assert [gift["id"] for gift in gifts] == [101, 102, 103]
