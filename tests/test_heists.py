import asyncio
import sqlite3

import pytest

import db as database
from miniapp import server


def _seed_heist_db(path):
    asyncio.run(database.init_db(str(path)))
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO economy (user_id,username,full_name,balance) "
            "VALUES (?,?,?,?)",
            [
                (1, "leader", "Leader", 20_000_000),
                (2, "hacker", "Hacker", 20_000_000),
                (3, "driver", "Driver", 20_000_000),
                (4, "muscle", "Muscle", 20_000_000),
                (5, "insider", "Insider", 20_000_000),
                (6, "rookie", "Rookie", 20_000_000),
            ],
        )
        conn.execute(
            "UPDATE anon_numbers SET owner_id=1, acquired_at=1 WHERE id=124"
        )
        conn.execute("UPDATE economy SET pinned_anon_id=124 WHERE user_id=1")
        conn.executemany(
            """INSERT INTO underground_inventory
               (user_id,item_key,quantity,updated_at) VALUES (?,?,?,1)""",
            [
                (2, "laptop", 1),
                (3, "stolen_car_keys", 3),
                (4, "stolen_pistol", 1),
                (5, "insider_targ", 1),
                (5, "insider_gas", 1),
                (5, "insider_sim", 1),
                (5, "insider_convoy", 1),
            ],
        )
        conn.commit()


@pytest.fixture
def heist_db(tmp_path, monkeypatch):
    path = tmp_path / "heists.db"
    _seed_heist_db(path)
    monkeypatch.setattr(server, "DB_PATH", str(path))
    monkeypatch.setattr(server, "_send_telegram_dm", lambda *_args, **_kwargs: None)
    return path


def _create(heist_key="scam_call", leader_id=1, leader_role="mastermind"):
    return server.create_heist(
        server.HeistCreateRequest(
            leader_id=leader_id,
            heist_key=heist_key,
            leader_role=leader_role,
        ),
        authenticated_user=leader_id,
    )


def _invite(heist_id, target_id, role, leader_id=1):
    return server.invite_to_heist(
        heist_id,
        server.HeistInviteRequest(
            leader_id=leader_id,
            target_id=target_id,
            role=role,
        ),
        authenticated_user=leader_id,
    )


def _accept(heist_id, user_id):
    return server.respond_to_heist_invitation(
        heist_id,
        server.HeistInvitationResponse(user_id=user_id, accept=True),
        authenticated_user=user_id,
    )


def _start_task(heist_id, user_id):
    result = server.start_heist_task(
        heist_id,
        server.HeistActorRequest(user_id=user_id),
        authenticated_user=user_id,
    )
    return result["heist"]["own_member"]["challenge"]


def _resolve(heist_id, user_id, answers):
    return server.resolve_heist_task(
        heist_id,
        server.HeistTaskResolveRequest(user_id=user_id, answers=answers),
        authenticated_user=user_id,
    )


def test_black_market_uses_anon_alias_without_discount(heist_db):
    before = server.black_market_status(user_id=1, authenticated_user=1)
    assert before["identity"]["alias"] == "+888 124"
    assert before["anon_discount"] == 0

    bought = server.black_market_buy(
        server.BlackMarketBuyRequest(
            user_id=1,
            item_key="hacking_usb",
            quantity=1,
        ),
        authenticated_user=1,
    )
    assert bought["new_balance"] == 19_650_000
    assert bought["anon_discount"] == 0

    with sqlite3.connect(heist_db) as conn:
        purchase = conn.execute(
            """SELECT buyer_alias,buyer_anon,total_paid
               FROM black_market_purchases"""
        ).fetchone()
        assert purchase == ("+888 124", 1, 350_000)


def test_scam_call_crew_completes_role_tasks_and_keeps_intel(heist_db):
    created = _create()
    heist_id = created["heist"]["id"]
    assert created["heist"]["members"][0]["alias"] == "+888 124"
    assert created["new_balance"] == 19_975_000

    _invite(heist_id, 2, "hacker")
    accepted = _accept(heist_id, 2)
    assert accepted["new_balance"] == 19_975_000
    started = server.start_heist(
        heist_id,
        server.HeistActorRequest(user_id=1),
        authenticated_user=1,
    )
    assert 180_000 <= started["base_payout"] <= 260_000

    casing = _start_task(heist_id, 1)
    first = _resolve(heist_id, 1, casing["zones"])
    assert first["result"] == "success"
    assert first["settlement"] is None

    chip = _start_task(heist_id, 2)
    final = _resolve(heist_id, 2, chip["gates"])
    assert final["result"] == "success"
    assert final["settlement"]["status"] == "completed"
    assert final["settlement"]["bonus"] == 0

    with sqlite3.connect(heist_db) as conn:
        assert conn.execute(
            "SELECT status FROM heists WHERE id=?", (heist_id,)
        ).fetchone()[0] == "completed"
        for user_id in (1, 2):
            assert conn.execute(
                """SELECT quantity FROM underground_inventory
                   WHERE user_id=? AND item_key='insider_targ'""",
                (user_id,),
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT heat FROM economy WHERE user_id=?", (user_id,)
            ).fetchone()[0] == 20


def test_inside_contact_can_lead_scam_and_adds_bonus(heist_db):
    created = _create(leader_id=5, leader_role="insider")
    heist_id = created["heist"]["id"]
    _invite(heist_id, 2, "hacker", leader_id=5)
    _accept(heist_id, 2)
    server.start_heist(
        heist_id,
        server.HeistActorRequest(user_id=5),
        authenticated_user=5,
    )

    casing = _start_task(heist_id, 5)
    _resolve(heist_id, 5, casing["zones"])
    chip = _start_task(heist_id, 2)
    final = _resolve(heist_id, 2, chip["gates"])

    assert final["settlement"]["status"] == "completed"
    assert final["settlement"]["bonus"] > 0
    with sqlite3.connect(heist_db) as conn:
        # The charge is spent when the heist starts and retained again after
        # successful completion.
        assert conn.execute(
            """SELECT quantity FROM underground_inventory
               WHERE user_id=5 AND item_key='insider_targ'"""
        ).fetchone()[0] == 1


def test_gas_station_requires_driver_muscle_and_consumes_keys(heist_db):
    created = _create("gas_station")
    heist_id = created["heist"]["id"]
    _invite(heist_id, 3, "driver")
    _invite(heist_id, 4, "muscle")
    _accept(heist_id, 3)
    _accept(heist_id, 4)
    server.start_heist(
        heist_id,
        server.HeistActorRequest(user_id=1),
        authenticated_user=1,
    )

    with sqlite3.connect(heist_db) as conn:
        assert conn.execute(
            """SELECT quantity FROM underground_inventory
               WHERE user_id=3 AND item_key='stolen_car_keys'"""
        ).fetchone()[0] == 2
        assert conn.execute(
            """SELECT quantity FROM underground_inventory
               WHERE user_id=4 AND item_key='stolen_pistol'"""
        ).fetchone()[0] == 1

    casing = _start_task(heist_id, 1)
    _resolve(heist_id, 1, casing["zones"])
    getaway = _start_task(heist_id, 3)
    safe_lanes = [(lane + 1) % 3 for lane in getaway["obstacles"]]
    _resolve(heist_id, 3, safe_lanes)
    crowd = _start_task(heist_id, 4)
    answers = [
        "control" if prompt == "guard" else "hold"
        for prompt in crowd["prompts"]
    ]
    final = _resolve(heist_id, 4, answers)
    assert final["settlement"]["status"] == "completed"


def test_failed_assignment_fails_heist_and_burns_stakes(heist_db):
    created = _create()
    heist_id = created["heist"]["id"]
    _invite(heist_id, 2, "hacker")
    _accept(heist_id, 2)
    server.start_heist(
        heist_id,
        server.HeistActorRequest(user_id=1),
        authenticated_user=1,
    )
    _start_task(heist_id, 1)
    failed = _resolve(heist_id, 1, [99, 98, 97])
    assert failed["result"] == "failed"
    assert failed["settlement"]["status"] == "failed"

    with sqlite3.connect(heist_db) as conn:
        assert conn.execute(
            "SELECT status FROM heists WHERE id=?", (heist_id,)
        ).fetchone()[0] == "failed"
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=1"
        ).fetchone()[0] == 19_975_000
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=2"
        ).fetchone()[0] == 19_975_000


def test_role_tools_and_alternate_planner_slot_are_enforced(heist_db):
    created = _create()
    heist_id = created["heist"]["id"]

    with pytest.raises(Exception, match="alternate role"):
        _invite(heist_id, 5, "insider")
    with pytest.raises(Exception, match="needs Unmarked Laptop"):
        _invite(heist_id, 6, "hacker")


def test_anon_crew_member_conceals_account_id_but_not_role_or_alias(heist_db):
    created = _create()
    heist_id = created["heist"]["id"]
    _invite(heist_id, 2, "hacker")
    _accept(heist_id, 2)

    viewed_by_hacker = server.heist_status(
        user_id=2,
        authenticated_user=2,
    )["current_heist"]
    leader = next(
        member
        for member in viewed_by_hacker["members"]
        if member["role"] == "mastermind"
    )
    assert leader["alias"] == "+888 124"
    assert leader["anonymous"] is True
    assert leader["user_id"] is None
    assert viewed_by_hacker["leader_id"] is None


def test_disbanding_forming_crew_refunds_all_accepted_stakes(heist_db):
    created = _create()
    heist_id = created["heist"]["id"]
    _invite(heist_id, 2, "hacker")
    _accept(heist_id, 2)

    result = server.cancel_heist(
        heist_id,
        server.HeistActorRequest(user_id=1),
        authenticated_user=1,
    )
    assert result["stakes_refunded"] is True
    with sqlite3.connect(heist_db) as conn:
        balances = conn.execute(
            "SELECT user_id,balance FROM economy WHERE user_id IN (1,2)"
        ).fetchall()
        assert balances == [(1, 20_000_000), (2, 20_000_000)]


def test_leader_can_withdraw_invite_or_remove_member_with_refund(heist_db):
    created = _create()
    heist_id = created["heist"]["id"]
    _invite(heist_id, 2, "hacker")
    invited = server.heist_status(
        user_id=1,
        authenticated_user=1,
    )["current_heist"]["members"][-1]
    withdrawn = server.remove_heist_member(
        heist_id,
        invited["member_id"],
        server.HeistActorRequest(user_id=1),
        authenticated_user=1,
    )
    assert withdrawn["stake_refunded"] == 0

    _invite(heist_id, 2, "hacker")
    _accept(heist_id, 2)
    accepted = server.heist_status(
        user_id=1,
        authenticated_user=1,
    )["current_heist"]["members"][-1]
    removed = server.remove_heist_member(
        heist_id,
        accepted["member_id"],
        server.HeistActorRequest(user_id=1),
        authenticated_user=1,
    )
    assert removed["stake_refunded"] == 25_000
    with sqlite3.connect(heist_db) as conn:
        assert conn.execute(
            "SELECT balance FROM economy WHERE user_id=2"
        ).fetchone()[0] == 20_000_000


def test_durable_market_tools_have_no_repeat_purchase_or_task_bonus(heist_db):
    server.black_market_buy(
        server.BlackMarketBuyRequest(
            user_id=1,
            item_key="stolen_pistol",
            quantity=1,
        ),
        authenticated_user=1,
    )
    with pytest.raises(Exception, match="at most 1"):
        server.black_market_buy(
            server.BlackMarketBuyRequest(
                user_id=1,
                item_key="stolen_pistol",
                quantity=1,
            ),
            authenticated_user=1,
        )
    catalog = server.black_market_status(
        user_id=1,
        authenticated_user=1,
    )["catalog"]
    rifle = next(item for item in catalog if item["key"] == "stolen_rifle")
    assert "No task advantage" in rifle["description"]


def test_crypto_heists_are_available_with_optional_insider(heist_db):
    status = server.heist_status(user_id=1, authenticated_user=1)
    types = {item["key"]: item for item in status["types"]}

    assert types["sim_swap"]["required_groups"] == [
        ["mastermind"],
        ["hacker"],
        ["driver"],
    ]
    assert types["sim_swap"]["optional_roles"] == ["insider"]
    assert types["cold_wallet_convoy"]["required_groups"] == [
        ["mastermind"],
        ["hacker"],
        ["muscle"],
        ["driver"],
    ]
    assert status["anon_changes_gameplay"] is False
