import asyncio
import sqlite3

import db as database
from miniapp import server


def _seed_wallet(path, balance=10_000):
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO economy (user_id, username, full_name, balance) "
            "VALUES (1, 'player', 'Player', ?)",
            (balance,),
        )
        conn.commit()


def _rigged_deck(*dealt_cards):
    """Build a deck whose pop order matches dealt_cards."""
    return list(reversed(dealt_cards))


def test_perfect_pair_uses_tuple_cards_and_credits_full_payout(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "blackjack-pair.db"
    _seed_wallet(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    server._bj_games.clear()
    monkeypatch.setattr(
        server,
        "_bj_new_deck",
        lambda: _rigged_deck(
            ("K", "♥"),
            ("K", "♦"),
            ("9", "♠"),
            ("7", "♣"),
        ),
    )

    result = server.blackjack_start(
        server.BlackjackStartRequest(user_id=1, bet=100, pp_bet=500),
        authenticated_user=1,
    )

    assert result["status"] == "playing"
    assert result["pp_result"] == "colored"
    assert result["pp_delta"] == 1_500
    # 10,000 - 100 main - 500 side + 2,000 side payout.
    assert result["balance"] == 11_400


def test_blackjack_natural_pays_three_to_two(tmp_path, monkeypatch):
    path = tmp_path / "blackjack-natural.db"
    _seed_wallet(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    server._bj_games.clear()
    monkeypatch.setattr(
        server,
        "_bj_new_deck",
        lambda: _rigged_deck(
            ("A", "♥"),
            ("K", "♦"),
            ("9", "♠"),
            ("7", "♣"),
        ),
    )

    result = server.blackjack_start(
        server.BlackjackStartRequest(user_id=1, bet=100),
        authenticated_user=1,
    )

    assert result["status"] == "blackjack"
    assert result["total_delta"] == 150
    assert result["new_balance"] == 10_150


def test_dealer_natural_resolves_before_player_actions(tmp_path, monkeypatch):
    path = tmp_path / "blackjack-dealer-natural.db"
    _seed_wallet(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    server._bj_games.clear()
    monkeypatch.setattr(
        server,
        "_bj_new_deck",
        lambda: _rigged_deck(
            ("10", "♥"),
            ("9", "♦"),
            ("A", "♠"),
            ("K", "♣"),
        ),
    )

    result = server.blackjack_start(
        server.BlackjackStartRequest(user_id=1, bet=100),
        authenticated_user=1,
    )

    assert result["status"] == "finished"
    assert result["results"][0]["outcome"] == "lose"
    assert result["new_balance"] == 9_900
    assert 1 not in server._bj_games


def test_double_deducts_second_stake_before_payout(tmp_path, monkeypatch):
    path = tmp_path / "blackjack-double.db"
    _seed_wallet(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    server._bj_games.clear()
    monkeypatch.setattr(
        server,
        "_bj_new_deck",
        lambda: _rigged_deck(
            ("5", "♥"),
            ("6", "♦"),
            ("9", "♠"),
            ("7", "♣"),
            ("K", "♣"),
            ("4", "♦"),
        ),
    )

    server.blackjack_start(
        server.BlackjackStartRequest(user_id=1, bet=100),
        authenticated_user=1,
    )
    result = server.blackjack_action(
        server.BlackjackActionRequest(user_id=1, action="double"),
        authenticated_user=1,
    )

    assert result["status"] == "finished"
    assert result["results"][0]["hand_bet"] == 200
    assert result["total_delta"] == 200
    # Two 100 stakes are deducted and the winning doubled hand pays 400.
    assert result["new_balance"] == 10_200


def test_plinko_can_settle_multiple_balls_atomically(tmp_path, monkeypatch):
    path = tmp_path / "plinko-multi.db"
    _seed_wallet(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    choices = iter(
        [False] * 8 +                         # slot 0: 2.2x
        [True, True, True, True] + [False] * 4  # slot 4: 0.65x
    )
    monkeypatch.setattr(server.random, "choice", lambda _options: next(choices))

    result = server.play_plinko(
        server.PlinkoRequest(user_id=1, bet=100, risk="low", balls=2),
        authenticated_user=1,
    )

    assert result["balls"] == 2
    assert result["total_bet"] == 200
    assert [drop["slot"] for drop in result["drops"]] == [0, 4]
    assert result["total_delta"] == 85
    assert result["new_balance"] == 10_085
    with sqlite3.connect(path) as conn:
        stats = conn.execute(
            "SELECT plinko_won, plinko_lost FROM game_stats WHERE user_id = 1"
        ).fetchone()
    assert stats == (120, 100)


def test_roulette_straight_double_zero_pays_36_to_1(tmp_path, monkeypatch):
    path = tmp_path / "roulette-straight.db"
    _seed_wallet(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    # Wheel slot 19 is 00 (represented by -1 server-side).
    monkeypatch.setattr(server.random, "randint", lambda _low, _high: 19)

    result = server.play_roulette(
        server.RouletteRequest(
            user_id=1,
            bet=100,
            bet_type="straight",
            straight_number=-1,
        ),
        authenticated_user=1,
    )

    assert result["winning_number"] == "00"
    assert result["won"] is True
    assert result["payout_mult"] == 36
    assert result["delta"] == 3_500
    assert result["new_balance"] == 13_500
