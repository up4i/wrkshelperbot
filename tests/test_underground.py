import asyncio
import sqlite3

import pytest

import db as database
from miniapp import server


def _seed_underground_db(path):
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO economy (user_id,username,full_name,balance) "
            "VALUES (?,?,?,?)",
            [
                (1, "poster", "Poster", 1_000_000),
                (2, "target", "Target", 1_000_000),
                (3, "ghost", "Ghost", 1_000_000),
                (4, "public", "Public Hunter", 1_000_000),
            ],
        )
        conn.execute(
            "UPDATE anon_numbers SET owner_id=1, acquired_at=1 WHERE id=124"
        )
        conn.execute(
            "UPDATE anon_numbers SET owner_id=3, acquired_at=1 WHERE id=126"
        )
        conn.execute("UPDATE economy SET pinned_anon_id=124 WHERE user_id=1")
        conn.execute("UPDATE economy SET pinned_anon_id=126 WHERE user_id=3")
        conn.commit()


@pytest.fixture
def underground_db(tmp_path, monkeypatch):
    path = tmp_path / "underground.db"
    _seed_underground_db(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    monkeypatch.setattr(server, "_send_telegram_dm", lambda *_args, **_kwargs: None)
    return path


def _post(creator_id=1, target_id=2, amount=100_000):
    return server.create_underground_bounty(
        server.UndergroundBountyCreateRequest(
            creator_id=creator_id,
            target_id=target_id,
            amount=amount,
        ),
        authenticated_user=creator_id,
    )


def test_anon_number_conceals_identity_without_changing_contract_rules(
    underground_db,
):
    posted = _post()
    assert posted["bounty"]["creator"] == "+888 124"
    assert posted["bounty"]["creator_anonymous"] is True
    assert posted["new_balance"] == 895_000

    board = server.underground_status(user_id=3, authenticated_user=3)
    assert board["identity"]["alias"] == "+888 126"
    assert board["identity"]["anonymous"] is True
    assert board["rules"]["anon_changes_gameplay"] is False
    assert board["open_bounties"][0]["creator"] == "+888 124"

    claimed = server.claim_underground_bounty(
        posted["bounty"]["id"],
        server.UndergroundActorRequest(user_id=3),
        authenticated_user=3,
    )
    sequence = claimed["contract"]["challenge_sequence"]
    assert len(sequence) == 5
    assert claimed["anon_changes_gameplay"] is False
    assert claimed["challenge_seconds"] == 45

    result = server.resolve_underground_bounty(
        posted["bounty"]["id"],
        server.UndergroundResolveRequest(user_id=3, sequence=sequence),
        authenticated_user=3,
    )
    assert result["result"] == "success"
    assert result["payout"] == 100_000
    assert result["new_balance"] == 1_100_000
    assert result["heat"] == 25

    with sqlite3.connect(underground_db) as conn:
        # The bounty is escrowed by its creator; completing it never takes
        # extra funds from the target.
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=2"
        ).fetchone()[0] == 1_000_000
        row = conn.execute(
            "SELECT status,hunter_alias,hunter_anon FROM underground_bounties"
        ).fetchone()
        assert row == ("completed", "+888 126", 1)


def test_public_and_anon_hunters_receive_the_same_trace_rules(underground_db):
    first = _post()
    anon_claim = server.claim_underground_bounty(
        first["bounty"]["id"],
        server.UndergroundActorRequest(user_id=3),
        authenticated_user=3,
    )
    server.resolve_underground_bounty(
        first["bounty"]["id"],
        server.UndergroundResolveRequest(user_id=3, sequence=["wrong"] * 5),
        authenticated_user=3,
    )

    second = _post(creator_id=2, target_id=1)
    public_claim = server.claim_underground_bounty(
        second["bounty"]["id"],
        server.UndergroundActorRequest(user_id=4),
        authenticated_user=4,
    )
    assert len(anon_claim["contract"]["challenge_sequence"]) == 5
    assert len(public_claim["contract"]["challenge_sequence"]) == 5
    assert anon_claim["preview_seconds"] == public_claim["preview_seconds"] == 3
    assert anon_claim["challenge_seconds"] == public_claim["challenge_seconds"] == 45
    assert anon_claim["anon_changes_gameplay"] is False
    assert public_claim["anon_changes_gameplay"] is False


def test_failed_trace_reopens_bounty_and_hunter_cannot_farm_retry(
    underground_db,
):
    posted = _post()
    bounty_id = posted["bounty"]["id"]
    server.claim_underground_bounty(
        bounty_id,
        server.UndergroundActorRequest(user_id=4),
        authenticated_user=4,
    )
    failed = server.resolve_underground_bounty(
        bounty_id,
        server.UndergroundResolveRequest(
            user_id=4,
            sequence=["wrong"] * 5,
        ),
        authenticated_user=4,
    )
    assert failed["result"] == "failed"
    assert failed["payout"] == 0
    assert failed["heat"] == 8

    with sqlite3.connect(underground_db) as conn:
        assert conn.execute(
            "SELECT status,hunter_id FROM underground_bounties WHERE id=?",
            (bounty_id,),
        ).fetchone() == ("open", None)

    with pytest.raises(Exception, match="already attempted"):
        server.claim_underground_bounty(
            bounty_id,
            server.UndergroundActorRequest(user_id=4),
            authenticated_user=4,
        )


def test_cancel_refunds_escrow_but_burns_fee_and_claim_rules_hold(
    underground_db,
):
    posted = _post()
    bounty_id = posted["bounty"]["id"]

    for user_id in (1, 2):
        with pytest.raises(Exception, match="cannot claim"):
            server.claim_underground_bounty(
                bounty_id,
                server.UndergroundActorRequest(user_id=user_id),
                authenticated_user=user_id,
            )

    cancelled = server.cancel_underground_bounty(
        bounty_id,
        server.UndergroundActorRequest(user_id=1),
        authenticated_user=1,
    )
    assert cancelled["new_balance"] == 995_000
    assert cancelled["fee_refunded"] is False

    with sqlite3.connect(underground_db) as conn:
        assert conn.execute(
            "SELECT status FROM underground_bounties WHERE id=?",
            (bounty_id,),
        ).fetchone()[0] == "cancelled"


def test_underground_requires_authenticated_actor(underground_db):
    with pytest.raises(Exception):
        server.create_underground_bounty(
            server.UndergroundBountyCreateRequest(
                creator_id=1,
                target_id=2,
                amount=100_000,
            ),
            authenticated_user=4,
        )


def test_heat_decays_and_bot_helper_uses_the_same_scale(underground_db):
    first = asyncio.run(database.add_heat(str(underground_db), 4, 20, now=10_000))
    decayed = asyncio.run(
        database.add_heat(str(underground_db), 4, 5, now=10_000 + 30 * 60)
    )
    capped = asyncio.run(
        database.add_heat(str(underground_db), 4, 500, now=10_000 + 30 * 60)
    )

    assert first == 20
    assert decayed == 24
    assert capped == 100


def test_expired_bounty_returns_escrow_but_not_fee(underground_db):
    posted = _post()
    with sqlite3.connect(underground_db) as conn:
        conn.execute(
            "UPDATE underground_bounties SET expires_at=1 WHERE id=?",
            (posted["bounty"]["id"],),
        )
        conn.commit()

    server.underground_status(user_id=1, authenticated_user=1)
    with sqlite3.connect(underground_db) as conn:
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=1"
        ).fetchone()[0] == 995_000
        assert conn.execute(
            "SELECT status FROM underground_bounties WHERE id=?",
            (posted["bounty"]["id"],),
        ).fetchone()[0] == "expired"
