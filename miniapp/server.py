import asyncio
import base64
import gzip
import hashlib
import hmac
import json
import random
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from collectibles import (
    ANON_FIREWALL_COOLDOWN,
    ANON_MAX_SUFFIX,
    ANON_MIN_SUFFIX,
    ANON_VAULT_WITHDRAW_DELAY,
    anon_number_price,
    anon_number_rarity,
    format_anon_number,
)

DB_PATH = config.DB_PATH
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    background_tasks = await _startup()
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)


app = FastAPI(title="wrk.money mini-app", lifespan=_lifespan)

_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_SESSION_KEY = hmac.new(
    config.BOT_TOKEN.encode(),
    b"wrk.money mini-app session v1",
    hashlib.sha256,
).digest()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _issue_session_token(user_id: int, *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = _b64url_encode(json.dumps(
        {"user_id": int(user_id), "iat": issued_at, "exp": issued_at + _SESSION_TTL_SECONDS},
        separators=(",", ":"),
        sort_keys=True,
    ).encode())
    signature = hmac.new(_SESSION_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _verify_session_token(token: str, *, now: int | None = None) -> int:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(_SESSION_KEY, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        data = json.loads(_b64url_decode(payload))
        current_time = int(time.time()) if now is None else now
        if int(data["exp"]) < current_time:
            raise ValueError("expired")
        return int(data["user_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(401, "Telegram session is invalid or expired") from exc


def _authenticated_user(request: Request) -> int:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Open this mini-app from Telegram to sign in")
    return _verify_session_token(token)


AuthenticatedUser = Annotated[int, Depends(_authenticated_user)]


def _require_actor(authenticated_user: int, claimed_user: int) -> None:
    if authenticated_user != int(claimed_user):
        raise HTTPException(403, "You cannot act as another Telegram user")


def _require_owner(authenticated_user: int) -> None:
    if authenticated_user != config.OWNER_ID:
        raise HTTPException(403, "Owner access required")


def _websocket_protocol(websocket: WebSocket) -> tuple[str, str] | None:
    for protocol in websocket.headers.get("sec-websocket-protocol", "").split(","):
        protocol = protocol.strip()
        if protocol.startswith("wrk-auth."):
            return protocol, protocol.removeprefix("wrk-auth.")
    return None


async def _accept_authenticated_websocket(websocket: WebSocket) -> int | None:
    protocol_and_token = _websocket_protocol(websocket)
    if not protocol_and_token:
        await websocket.close(code=1008, reason="Telegram session required")
        return None
    protocol, token = protocol_and_token
    try:
        user_id = _verify_session_token(token)
    except HTTPException:
        await websocket.close(code=1008, reason="Telegram session expired")
        return None
    await websocket.accept(subprotocol=protocol)
    return user_id


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _display_name(row) -> str:
    if row["username"]:
        return f"@{row['username']}"
    return row["full_name"] or f"User {row['user_id']}"


def _active_security_number(db, user_id: int):
    return db.execute(
        """SELECT a.id, a.suffix
           FROM economy e
           JOIN anon_numbers a
             ON a.id = e.pinned_anon_id AND a.owner_id = e.user_id
           WHERE e.user_id = ?""",
        (user_id,),
    ).fetchone()


def _public_identity(db, user_id: int, fallback: str | None = None) -> str:
    row = db.execute(
        """SELECT e.username, e.full_name, e.anon_mask_enabled, a.suffix
           FROM economy e
           LEFT JOIN anon_numbers a
             ON a.id = e.pinned_anon_id AND a.owner_id = e.user_id
           WHERE e.user_id = ?""",
        (user_id,),
    ).fetchone()
    if not row:
        return fallback or f"User {user_id}"
    if row["anon_mask_enabled"] and row["suffix"] is not None:
        return format_anon_number(row["suffix"])
    if row["username"]:
        return f"@{row['username']}"
    return row["full_name"] or fallback or f"User {user_id}"


def _record_security_event(
    db,
    user_id: int,
    event_type: str,
    detail: str,
    *,
    amount: int = 0,
    actor_id: int | None = None,
    created_at: int | None = None,
) -> None:
    db.execute(
        """INSERT INTO anon_security_events
           (user_id, event_type, detail, amount, actor_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            event_type,
            detail,
            amount,
            actor_id,
            int(time.time()) if created_at is None else created_at,
        ),
    )


def _security_status(db, user_id: int, *, event_limit: int = 8) -> dict:
    row = db.execute(
        """SELECT e.balance, e.pinned_anon_id, e.secure_vault_balance,
                  e.vault_pending_amount, e.vault_withdraw_available_at,
                  e.anon_mask_enabled, e.anon_firewall_used_at,
                  a.suffix
           FROM economy e
           LEFT JOIN anon_numbers a
             ON a.id = e.pinned_anon_id AND a.owner_id = e.user_id
           WHERE e.user_id = ?""",
        (user_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "User not found — use the bot first")
    now = int(time.time())
    active = row["suffix"] is not None
    firewall_remaining = (
        max(
            0,
            ANON_FIREWALL_COOLDOWN
            - (now - (row["anon_firewall_used_at"] or 0)),
        )
        if active
        else 0
    )
    events = db.execute(
        """SELECT id, event_type, detail, amount, actor_id, created_at
           FROM anon_security_events
           WHERE user_id = ?
           ORDER BY created_at DESC, id DESC LIMIT ?""",
        (user_id, max(0, min(event_limit, 50))),
    ).fetchall()
    return {
        "active": active,
        "active_anon_id": row["pinned_anon_id"] if active else None,
        "active_number": (
            format_anon_number(row["suffix"]) if active else None
        ),
        "mask_enabled": bool(row["anon_mask_enabled"]) and active,
        "balance": row["balance"],
        "vault_balance": row["secure_vault_balance"],
        "pending_amount": row["vault_pending_amount"],
        "withdraw_available_at": row["vault_withdraw_available_at"],
        "withdraw_ready": bool(row["vault_pending_amount"])
        and now >= row["vault_withdraw_available_at"],
        "withdraw_delay": ANON_VAULT_WITHDRAW_DELAY,
        "firewall_ready": active and firewall_remaining == 0,
        "firewall_remaining": firewall_remaining,
        "events": [dict(event) for event in events],
    }


def _consume_anon_firewall(
    db,
    user_id: int,
    actor_id: int,
    *,
    now: int,
) -> dict:
    row = db.execute(
        """SELECT e.anon_firewall_used_at, a.suffix
           FROM economy e
           JOIN anon_numbers a
             ON a.id = e.pinned_anon_id AND a.owner_id = e.user_id
           WHERE e.user_id = ?""",
        (user_id,),
    ).fetchone()
    if not row:
        return {"blocked": False, "active": False, "remaining": 0}
    remaining = max(
        0,
        ANON_FIREWALL_COOLDOWN
        - (now - (row["anon_firewall_used_at"] or 0)),
    )
    if remaining:
        return {
            "blocked": False,
            "active": True,
            "remaining": remaining,
        }
    db.execute(
        "UPDATE economy SET anon_firewall_used_at = ? WHERE user_id = ?",
        (now, user_id),
    )
    _record_security_event(
        db,
        user_id,
        "firewall",
        "Blocked an incoming robbery",
        actor_id=actor_id,
        created_at=now,
    )
    return {
        "blocked": True,
        "active": True,
        "remaining": ANON_FIREWALL_COOLDOWN,
        "number": format_anon_number(row["suffix"]),
    }


def _assert_anon_transferable(db, user_id: int, anon_id: int) -> None:
    secured = db.execute(
        """SELECT 1 FROM economy
           WHERE user_id = ? AND pinned_anon_id = ?
             AND (secure_vault_balance > 0 OR vault_pending_amount > 0)""",
        (user_id, anon_id),
    ).fetchone()
    if secured:
        raise HTTPException(
            400,
            "This number secures vault funds and cannot be traded yet",
        )


# ── Leaderboard ───────────────────────────────────────────────────────────────

@app.get("/api/leaderboard")
def leaderboard(tab: str = "balance", limit: int = 20):
    masked_aliases: dict[int, str] = {}
    _name_subquery = """
        LEFT JOIN (
            SELECT user_id,
                   COALESCE(full_name, '') AS full_name,
                   username
            FROM user_activity
            WHERE (user_id, last_seen) IN (
                SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
            )
        ) a ON a.user_id = e.user_id
    """

    def _merged_name(r) -> str:
        if r["user_id"] in masked_aliases:
            return masked_aliases[r["user_id"]]
        username = r["a_username"] or r["e_username"]
        full_name = r["a_full_name"] or r["e_full_name"]
        if username:
            return f"@{username}"
        return full_name or f"User {r['user_id']}"

    with db_conn() as db:
        masked_aliases.update({
            row["user_id"]: format_anon_number(row["suffix"])
            for row in db.execute(
                """SELECT e.user_id, a.suffix
                   FROM economy e
                   JOIN anon_numbers a
                     ON a.id = e.pinned_anon_id AND a.owner_id = e.user_id
                   WHERE e.anon_mask_enabled = 1"""
            ).fetchall()
        })
        if tab == "balance":
            rows = db.execute(
                f"""SELECT e.user_id,
                           e.username AS e_username, e.full_name AS e_full_name,
                           a.username AS a_username, a.full_name AS a_full_name,
                           e.balance, e.streak
                    FROM economy e {_name_subquery}
                    ORDER BY e.balance DESC LIMIT ?""", (limit,)
            ).fetchall()
            return [{"rank": i + 1, "user_id": r["user_id"], "name": _merged_name(r),
                     "identity_masked": r["user_id"] in masked_aliases,
                     "value": r["balance"], "streak": r["streak"]} for i, r in enumerate(rows)]

        if tab == "streak":
            rows = db.execute(
                f"""SELECT e.user_id,
                           e.username AS e_username, e.full_name AS e_full_name,
                           a.username AS a_username, a.full_name AS a_full_name,
                           e.balance, e.streak
                    FROM economy e {_name_subquery}
                    ORDER BY e.streak DESC LIMIT ?""", (limit,)
            ).fetchall()
            return [{"rank": i + 1, "user_id": r["user_id"], "name": _merged_name(r),
                     "identity_masked": r["user_id"] in masked_aliases,
                     "value": r["streak"], "balance": r["balance"]} for i, r in enumerate(rows)]

        if tab == "gifts":
            rows = db.execute(
                f"""SELECT e.user_id,
                           e.username AS e_username, e.full_name AS e_full_name,
                           a.username AS a_username, a.full_name AS a_full_name,
                           e.balance, COUNT(gi.id) AS gift_count
                    FROM economy e
                    LEFT JOIN gift_instances gi ON gi.owner_id = e.user_id
                    {_name_subquery}
                    GROUP BY e.user_id ORDER BY gift_count DESC LIMIT ?""", (limit,)
            ).fetchall()
            return [{"rank": i + 1, "user_id": r["user_id"], "name": _merged_name(r),
                     "identity_masked": r["user_id"] in masked_aliases,
                     "value": r["gift_count"], "balance": r["balance"]} for i, r in enumerate(rows)]

        if tab == "networth":
            rows = db.execute(
                f"""SELECT e.user_id,
                           e.username AS e_username, e.full_name AS e_full_name,
                           a.username AS a_username, a.full_name AS a_full_name,
                           e.balance
                           + COALESCE(e.secure_vault_balance, 0)
                           + COALESCE(e.vault_pending_amount, 0)
                           + COALESCE(gv.gift_value, 0)
                           + COALESCE(av.anon_value, 0) AS value
                    FROM economy e
                    LEFT JOIN (
                        SELECT gi.owner_id, SUM(gp.current_price) AS gift_value
                        FROM gift_instances gi
                        JOIN gift_models gm ON gm.id = gi.model_id
                        JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background
                        GROUP BY gi.owner_id
                    ) gv ON gv.owner_id = e.user_id
                    LEFT JOIN (
                        SELECT owner_id, SUM(price) AS anon_value
                        FROM anon_numbers WHERE owner_id IS NOT NULL GROUP BY owner_id
                    ) av ON av.owner_id = e.user_id
                    {_name_subquery}
                    ORDER BY value DESC LIMIT ?""", (limit,)
            ).fetchall()
            return [{"rank": i + 1, "user_id": r["user_id"], "name": _merged_name(r),
                     "identity_masked": r["user_id"] in masked_aliases,
                     "value": r["value"]} for i, r in enumerate(rows)]

        # ── Game stat tabs ────────────────────────────────────────────────────
        _gs_col = {
            "gamble_won":  (
                "gs.slots_won+gs.coinflip_won+gs.blackjack_won+gs.crash_won"
                "+gs.duck_won+gs.marbles_won+gs.livebj_won+gs.poker_won"
                "+gs.roulette_won+gs.plinko_won+gs.wheel_won+gs.slider_won"
                "+gs.craps_won+gs.highlow_won+gs.cases_won"
            ),
            "gamble_lost": (
                "gs.slots_lost+gs.coinflip_lost+gs.blackjack_lost+gs.crash_lost"
                "+gs.duck_lost+gs.marbles_lost+gs.livebj_lost+gs.poker_lost"
                "+gs.roulette_lost+gs.plinko_lost+gs.wheel_lost+gs.slider_lost"
                "+gs.craps_lost+gs.highlow_lost+gs.cases_lost"
            ),
            "slots":       "gs.slots_won",
            "coinflip":    "gs.coinflip_won",
            "blackjack":   "gs.blackjack_won",
            "crash":       "gs.crash_won",
            "crash_mult":  "gs.crash_best_mult",
        }
        if tab not in _gs_col:
            raise HTTPException(400, "unknown tab")

        col = _gs_col[tab]
        rows = db.execute(
            f"""SELECT e.user_id,
                       e.username AS e_username, e.full_name AS e_full_name,
                       a.username AS a_username, a.full_name AS a_full_name,
                       ({col}) AS value
                FROM game_stats gs
                JOIN economy e ON e.user_id = gs.user_id
                {_name_subquery.replace('ON a.user_id = e.user_id', 'ON a.user_id = gs.user_id')}
                WHERE ({col}) > 0
                ORDER BY ({col}) DESC LIMIT ?""", (limit,)
        ).fetchall()
        return [{"rank": i + 1, "user_id": r["user_id"], "name": _merged_name(r),
                 "identity_masked": r["user_id"] in masked_aliases,
                 "value": r["value"]} for i, r in enumerate(rows)]


# ── Profile ───────────────────────────────────────────────────────────────────

def _profile_gift_page(
    db,
    user_id: int,
    gifts_offset: int = 0,
    gifts_limit: int = 20,
) -> list:
    gifts_offset = max(0, gifts_offset)
    gifts_limit = max(1, min(gifts_limit, 200))
    return db.execute(
        "SELECT gi.id, gi.gift_number, gi.background, gi.acquired_at, gi.is_admin_gift, "
        "gm.model_name, gm.model_emoji, gm.tier, gm.collection, gm.custom_emoji_id, "
        "COALESCE(gp.current_price, 0) AS current_price "
        "FROM gift_instances gi JOIN gift_models gm ON gm.id = gi.model_id "
        "LEFT JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background "
        "WHERE gi.owner_id = ? "
        "ORDER BY COALESCE(gi.sort_index, 999999) ASC, gi.acquired_at DESC "
        "LIMIT ? OFFSET ?",
        (user_id, gifts_limit, gifts_offset),
    ).fetchall()


def _load_profile(db, user_id: int, gifts_offset: int = 0, gifts_limit: int = 20) -> dict:
    row = db.execute(
        """SELECT e.user_id,
                  e.username  AS e_username,  e.full_name  AS e_full_name,
                  a.username  AS a_username,  a.full_name  AS a_full_name,
                  e.balance, e.streak, e.last_daily, e.pinned_gift_id, e.pinned_anon_id,
                  e.secure_vault_balance, e.vault_pending_amount,
                  e.anon_mask_enabled, security_anon.suffix AS security_suffix
           FROM economy e
           LEFT JOIN anon_numbers security_anon
             ON security_anon.id = e.pinned_anon_id
            AND security_anon.owner_id = e.user_id
           LEFT JOIN (
               SELECT user_id, username, full_name FROM user_activity
               WHERE (user_id, last_seen) IN (
                   SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
               )
           ) a ON a.user_id = e.user_id
           WHERE e.user_id = ?""", (user_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "User not found")
    username  = row["a_username"]  or row["e_username"]
    full_name = row["a_full_name"] or row["e_full_name"]
    identity_masked = bool(row["anon_mask_enabled"]) and row["security_suffix"] is not None
    display = (
        format_anon_number(row["security_suffix"])
        if identity_masked
        else f"@{username}" if username else (full_name or f"User {user_id}")
    )

    gifts_offset = max(0, gifts_offset)
    gifts_limit = max(1, min(gifts_limit, 200))
    gifts = _profile_gift_page(db, user_id, gifts_offset, gifts_limit)

    balance_rank = db.execute(
        "SELECT COUNT(*) + 1 FROM economy WHERE balance > ?", (row["balance"],)
    ).fetchone()[0]
    streak_rank = db.execute(
        "SELECT COUNT(*) + 1 FROM economy WHERE streak > ?", (row["streak"],)
    ).fetchone()[0]
    gift_summary = db.execute(
        "SELECT COUNT(*) AS gift_count, "
        "COALESCE(SUM(CASE WHEN COALESCE(is_admin_gift, 0) = 1 THEN 1 ELSE 0 END), 0) "
        "AS admin_gift_count "
        "FROM gift_instances WHERE owner_id = ?",
        (user_id,),
    ).fetchone()
    gift_count = gift_summary["gift_count"]
    admin_gift_count = gift_summary["admin_gift_count"]
    gift_rank = db.execute(
        "SELECT COUNT(*) + 1 FROM ("
        "SELECT owner_id, COUNT(*) AS c FROM gift_instances "
        "WHERE owner_id IS NOT NULL GROUP BY owner_id HAVING c > ?) ", (gift_count,)
    ).fetchone()[0]

    pinned_gift = None
    if row["pinned_gift_id"]:
        pg = db.execute(
            "SELECT gi.id, gi.gift_number, gi.background, gi.is_admin_gift, "
            "gm.model_name, gm.model_emoji, gm.custom_emoji_id, "
            "COALESCE(gp.current_price, 0) AS current_price "
            "FROM gift_instances gi JOIN gift_models gm ON gm.id = gi.model_id "
            "LEFT JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background "
            "WHERE gi.id = ? AND gi.owner_id = ?",
            (row["pinned_gift_id"], user_id),
        ).fetchone()
        pinned_gift = dict(pg) if pg else None

    pinned_anon = None
    if row["pinned_anon_id"]:
        pa = db.execute(
            "SELECT id, suffix, price FROM anon_numbers WHERE id = ? AND owner_id = ?",
            (row["pinned_anon_id"], user_id),
        ).fetchone()
        if pa:
            pinned_anon = dict(pa)
            pinned_anon["number"] = format_anon_number(pinned_anon["suffix"])
            pinned_anon["rarity"] = anon_number_rarity(pinned_anon["suffix"])[0]

    gift_value = db.execute(
        """SELECT COALESCE(SUM(gp.current_price), 0)
           FROM gift_instances gi
           JOIN gift_models gm ON gm.id = gi.model_id
           JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background
           WHERE gi.owner_id = ? AND COALESCE(gi.is_admin_gift, 0) = 0""",
        (user_id,)
    ).fetchone()[0]
    anon_value_row = db.execute(
        "SELECT COUNT(*) AS anon_count, COALESCE(SUM(price), 0) AS anon_value "
        "FROM anon_numbers WHERE owner_id = ?",
        (user_id,),
    ).fetchone()
    anon_count = anon_value_row["anon_count"]
    anon_value = anon_value_row["anon_value"]
    owned_anons = db.execute(
        "SELECT id, suffix, price, acquired_at FROM anon_numbers "
        "WHERE owner_id = ? ORDER BY suffix",
        (user_id,),
    ).fetchall()
    vault_value = row["secure_vault_balance"] + row["vault_pending_amount"]
    net_worth = row["balance"] + vault_value + gift_value + anon_value
    networth_rank = db.execute(
        """SELECT COUNT(*)+1 FROM (
               SELECT e.user_id,
                      e.balance
                      + COALESCE(e.secure_vault_balance, 0)
                      + COALESCE(e.vault_pending_amount, 0)
                      + COALESCE(gv.gift_value, 0)
                      + COALESCE(av.anon_value, 0) AS nw
               FROM economy e
               LEFT JOIN (
                   SELECT gi.owner_id, SUM(gp.current_price) AS gift_value
                   FROM gift_instances gi
                   JOIN gift_models gm ON gm.id = gi.model_id
                   JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background
                   WHERE COALESCE(gi.is_admin_gift,0)=0
                   GROUP BY gi.owner_id
               ) gv ON gv.owner_id = e.user_id
               LEFT JOIN (
                   SELECT owner_id, SUM(price) AS anon_value
                   FROM anon_numbers WHERE owner_id IS NOT NULL GROUP BY owner_id
               ) av ON av.owner_id = e.user_id
           ) WHERE nw > ?""",
        (net_worth,)
    ).fetchone()[0]

    tags = []

    # #1 Net Worth
    nw_rank = db.execute(
        """SELECT COUNT(*)+1 FROM (
               SELECT e.user_id,
                      e.balance
                      + COALESCE(e.secure_vault_balance, 0)
                      + COALESCE(e.vault_pending_amount, 0)
                      + COALESCE(gv.gift_value, 0)
                      + COALESCE(av.anon_value, 0) AS nw
               FROM economy e
               LEFT JOIN (
                   SELECT gi.owner_id, SUM(gp.current_price) AS gift_value
                   FROM gift_instances gi
                   JOIN gift_models gm ON gm.id = gi.model_id
                   JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background
                   GROUP BY gi.owner_id
               ) gv ON gv.owner_id = e.user_id
               LEFT JOIN (
                   SELECT owner_id, SUM(price) AS anon_value
                   FROM anon_numbers WHERE owner_id IS NOT NULL GROUP BY owner_id
               ) av ON av.owner_id = e.user_id
           ) WHERE nw > ?""", (net_worth,)
    ).fetchone()[0]
    if nw_rank == 1:
        tags.append("#1 Net Worth")

    # #1 Crash Mult
    mult_row = db.execute(
        "SELECT COUNT(*)+1 FROM game_stats WHERE crash_best_mult > "
        "(SELECT COALESCE(crash_best_mult,0) FROM game_stats WHERE user_id=?)",
        (user_id,)
    ).fetchone()
    if mult_row and mult_row[0] == 1:
        tags.append("#1 Crash Mult")

    # #1 [Collection] Holder
    top_holders = db.execute(
        """SELECT gm.collection, COUNT(*) AS cnt FROM gift_instances gi
           JOIN gift_models gm ON gm.id = gi.model_id
           WHERE gi.owner_id = ?
           GROUP BY gm.collection""", (user_id,)
    ).fetchall()
    for holder_row in top_holders:
        collection = holder_row["collection"]
        cnt = holder_row["cnt"]
        rank = db.execute(
            """SELECT COUNT(*)+1 FROM (
                   SELECT owner_id, COUNT(*) AS c FROM gift_instances gi2
                   JOIN gift_models gm2 ON gm2.id = gi2.model_id
                   WHERE gm2.collection=? AND owner_id IS NOT NULL
                   GROUP BY owner_id
               ) WHERE c > ?""",
            (collection, cnt)
        ).fetchone()[0]
        if rank == 1 and len(tags) < 3:
            display_col = collection.replace("_", " ").title()
            tags.append(f"#1 {display_col} Holder")
        if len(tags) >= 3:
            break

    # Pinned stat highlight
    row_ps = db.execute("SELECT pinned_stat FROM economy WHERE user_id=?", (user_id,)).fetchone()
    pinned_stat = row_ps["pinned_stat"] if row_ps and row_ps["pinned_stat"] else "crash_mult"
    d: dict = {}
    d["pinned_stat"] = pinned_stat

    gs = db.execute("SELECT * FROM game_stats WHERE user_id=?", (user_id,)).fetchone()
    if pinned_stat == "crash_mult":
        d["stat_highlight_label"] = "Best crash mult"
        d["stat_highlight_value"] = f"{gs['crash_best_mult']:.2f}×" if gs and gs["crash_best_mult"] else "—"
    elif pinned_stat == "gamble_won":
        total = 0
        if gs:
            won_cols = [c for c in gs.keys() if c.endswith("_won") and c != "crash_best_mult"]
            total = sum(gs[c] for c in won_cols)
        d["stat_highlight_label"] = "Total WRK$ won"
        d["stat_highlight_value"] = f"{total:,} WRK$"
    elif pinned_stat == "gamble_lost":
        total = 0
        if gs:
            lost_cols = [c for c in gs.keys() if c.endswith("_lost")]
            total = sum(gs[c] for c in lost_cols)
        d["stat_highlight_label"] = "Total WRK$ lost"
        d["stat_highlight_value"] = f"{total:,} WRK$"
    elif pinned_stat == "gifts_owned":
        cnt = db.execute("SELECT COUNT(*) FROM gift_instances WHERE owner_id=?", (user_id,)).fetchone()[0]
        d["stat_highlight_label"] = "Gifts owned"
        d["stat_highlight_value"] = str(cnt)
    elif pinned_stat == "streak":
        streak_row = db.execute("SELECT streak FROM economy WHERE user_id=?", (user_id,)).fetchone()
        d["stat_highlight_label"] = "Day streak"
        d["stat_highlight_value"] = f"{streak_row['streak']} days" if streak_row else "—"

    return {
        "user_id": row["user_id"],
        "name": display,
        "username": None if identity_masked else username,
        "identity_masked": identity_masked,
        "security_alias": (
            format_anon_number(row["security_suffix"])
            if row["security_suffix"] is not None
            else None
        ),
        "balance": row["balance"],
        "vault_value": vault_value,
        "streak": row["streak"],
        "last_daily": row["last_daily"],
        "balance_rank": balance_rank,
        "streak_rank": streak_rank,
        "gift_count": gift_count,
        "admin_gift_count": admin_gift_count,
        "gift_rank": gift_rank,
        "gift_value": gift_value,
        "anon_count": anon_count,
        "anon_value": anon_value,
        "anon_numbers": [_anon_item(anon) for anon in owned_anons],
        "net_worth": net_worth,
        "networth_rank": networth_rank,
        "gifts": [dict(g) for g in gifts],
        "pinned_gift": pinned_gift,
        "pinned_gift_id": row["pinned_gift_id"],
        "pinned_anon": pinned_anon,
        "pinned_anon_id": row["pinned_anon_id"],
        "has_more": len(gifts) == gifts_limit and (gifts_offset + gifts_limit) < gift_count,
        "tags": tags[:3],
        **d,
    }


@app.get("/api/profile/{user_id}")
def profile_by_id(user_id: int, gifts_offset: int = 0, gifts_limit: int = 20):
    with db_conn() as db:
        return _load_profile(db, user_id, gifts_offset, gifts_limit)


@app.get("/api/profile/username/{username}")
def profile_by_username(username: str, gifts_offset: int = 0, gifts_limit: int = 20):
    username = username.lstrip("@")
    with db_conn() as db:
        row = db.execute(
            "SELECT user_id FROM economy WHERE LOWER(username) = LOWER(?)", (username,)
        ).fetchone()
        if not row:
            row = db.execute(
                "SELECT user_id FROM user_activity WHERE LOWER(username) = LOWER(?) "
                "ORDER BY last_seen DESC LIMIT 1", (username,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Username not found")
        return _load_profile(db, row["user_id"], gifts_offset, gifts_limit)


@app.get("/api/profile/{user_id}/gifts")
def profile_gifts_page(user_id: int, offset: int = 0, limit: int = 20):
    offset = max(0, offset)
    limit = max(1, min(limit, 100))
    with db_conn() as db:
        exists = db.execute(
            "SELECT 1 FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "User not found")
        gifts = _profile_gift_page(db, user_id, offset, limit)
        gift_count = db.execute(
            "SELECT COUNT(*) FROM gift_instances WHERE owner_id = ?", (user_id,)
        ).fetchone()[0]
    next_offset = offset + len(gifts)
    return {
        "gifts": [dict(gift) for gift in gifts],
        "offset": offset,
        "next_offset": next_offset,
        "gift_count": gift_count,
        "has_more": next_offset < gift_count,
    }


# ── Pin gift ─────────────────────────────────────────────────────────────────

class PinGiftRequest(BaseModel):
    user_id: int
    gift_id: int | None = None


class ReorderRequest(BaseModel):
    user_id: int
    gift_ids: list[int]


@app.post("/api/profile/pin")
def pin_gift(req: PinGiftRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        if req.gift_id is not None:
            row = db.execute(
                "SELECT id FROM gift_instances WHERE id = ? AND owner_id = ?",
                (req.gift_id, req.user_id),
            ).fetchone()
            if not row:
                raise HTTPException(403, "You don't own this gift")
        db.execute(
            "UPDATE economy SET pinned_gift_id = ? WHERE user_id = ?",
            (req.gift_id, req.user_id),
        )
        db.commit()
    return {"ok": True}


_VALID_PINNED_STATS = {"crash_mult", "gamble_won", "gamble_lost", "gifts_owned", "streak"}


class PinnedStatRequest(BaseModel):
    user_id: int
    pinned_stat: str


@app.patch("/api/profile/stat")
def update_pinned_stat(req: PinnedStatRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if req.pinned_stat not in _VALID_PINNED_STATS:
        raise HTTPException(400, f"pinned_stat must be one of: {', '.join(_VALID_PINNED_STATS)}")
    with db_conn() as db:
        db.execute("UPDATE economy SET pinned_stat=? WHERE user_id=?", (req.pinned_stat, req.user_id))
        db.commit()
    return {"ok": True}


@app.post("/api/profile/reorder")
def profile_reorder(req: ReorderRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if not req.gift_ids:
        raise HTTPException(400, "Gift order cannot be empty")
    if len(req.gift_ids) != len(set(req.gift_ids)):
        raise HTTPException(400, "Gift order contains duplicates")
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        # Verify all gift_ids belong to this user
        placeholders = ",".join("?" * len(req.gift_ids))
        owned = db.execute(
            f"SELECT id FROM gift_instances WHERE id IN ({placeholders}) AND owner_id = ?",
            (*req.gift_ids, req.user_id)
        ).fetchall()
        if len(owned) != len(req.gift_ids):
            raise HTTPException(403, "One or more gifts don't belong to this user")
        db.executemany(
            "UPDATE gift_instances SET sort_index = ? "
            "WHERE id = ? AND owner_id = ?",
            [
                (idx, gift_id, req.user_id)
                for idx, gift_id in enumerate(req.gift_ids)
            ],
        )
        db.commit()
    return {"ok": True}


# ── Emoji image proxy ─────────────────────────────────────────────────────────

_EMOJI_CACHE = STATIC_DIR / "emoji_cache"
_EMOJI_CACHE.mkdir(exist_ok=True)
_EMOJI_ANIM_CACHE = STATIC_DIR / "emoji_anim_cache"
_EMOJI_ANIM_CACHE.mkdir(exist_ok=True)
_AVATAR_CACHE = STATIC_DIR / "avatar_cache"
_AVATAR_CACHE.mkdir(exist_ok=True)


@app.get("/emoji/{emoji_id}")
def get_emoji_image(emoji_id: str):
    if not emoji_id.isdigit():
        raise HTTPException(400, "Invalid emoji ID")

    cached = _EMOJI_CACHE / f"{emoji_id}.webp"
    if cached.exists():
        return FileResponse(str(cached), media_type="image/webp",
                            headers={"Cache-Control": "public, max-age=31536000"})

    token = config.BOT_TOKEN
    try:
        # 1. Resolve custom emoji → thumbnail file_id
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getCustomEmojiStickers",
            data=json.dumps({"custom_emoji_ids": [emoji_id]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        sticker = data["result"][0]
        thumb = sticker.get("thumbnail") or sticker.get("thumb")
        if not thumb:
            raise HTTPException(404, "No thumbnail")
        file_id = thumb["file_id"]

        # 2. Get file path
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}", timeout=8
        ) as r:
            file_data = json.loads(r.read())
        file_path = file_data["result"]["file_path"]

        # 3. Download and cache
        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=10
        ) as r:
            image_bytes = r.read()

        cached.write_bytes(image_bytes)
        return Response(content=image_bytes, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=31536000"})

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Could not fetch emoji from Telegram")


@app.get("/emoji-anim/{emoji_id}")
def get_emoji_animation(emoji_id: str):
    if not emoji_id.isdigit():
        raise HTTPException(400, "Invalid emoji ID")

    cached = _EMOJI_ANIM_CACHE / f"{emoji_id}.json"
    if cached.exists():
        return FileResponse(str(cached), media_type="application/json",
                            headers={"Cache-Control": "public, max-age=31536000"})

    token = config.BOT_TOKEN
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getCustomEmojiStickers",
            data=json.dumps({"custom_emoji_ids": [emoji_id]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        sticker = data["result"][0]

        if not sticker.get("is_animated"):
            raise HTTPException(404, "Sticker is not animated")

        file_id = sticker["file_id"]

        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}", timeout=8
        ) as r:
            file_data = json.loads(r.read())
        file_path = file_data["result"]["file_path"]

        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=10
        ) as r:
            tgs_bytes = r.read()

        lottie_json = gzip.decompress(tgs_bytes)
        cached.write_bytes(lottie_json)

        return Response(content=lottie_json, media_type="application/json",
                        headers={"Cache-Control": "public, max-age=31536000"})

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Could not fetch animation from Telegram")


# ── Avatar proxy ─────────────────────────────────────────────────────────────

@app.get("/api/user/badges/{user_id}")
def get_user_badges(user_id: int):
    owner_id = config.OWNER_ID
    with db_conn() as db:
        badges = []
        if user_id == owner_id:
            badges.append("owner")
        role_row = db.execute(
            "SELECT role FROM bot_roles WHERE user_id = ?", (user_id,)
        ).fetchone()
        if role_row:
            badges.append(role_row["role"])
        pepe_row = db.execute(
            """SELECT gi.owner_id FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gm.collection = 'plush_pepe' AND gi.gift_number = 1
               LIMIT 1"""
        ).fetchone()
        if pepe_row and pepe_row["owner_id"] == user_id:
            badges.append("plush_pepe_1")
    return {"badges": badges}


@app.get("/api/avatar/{user_id}")
def get_avatar(user_id: int):
    cached = _AVATAR_CACHE / f"{user_id}.jpg"
    if cached.exists():
        return FileResponse(str(cached), media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

    # Try stored photo_url (from initData when available)
    with db_conn() as db:
        row = db.execute("SELECT photo_url FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    photo_url = row["photo_url"] if row else None
    if photo_url:
        try:
            with urllib.request.urlopen(photo_url, timeout=8) as r:
                img_bytes = r.read()
            cached.write_bytes(img_bytes)
            return Response(content=img_bytes, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})
        except Exception:
            pass

    # Fall back to bot API (works for users with public profile photos)
    token = config.BOT_TOKEN
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getUserProfilePhotos",
            data=json.dumps({"user_id": user_id, "limit": 1}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        photos = data.get("result", {}).get("photos", [])
        if not photos:
            raise HTTPException(404, "No profile photo")
        file_id = photos[0][-1]["file_id"]

        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}", timeout=8
        ) as r:
            file_data = json.loads(r.read())
        file_path = file_data["result"]["file_path"]

        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=10
        ) as r:
            img_bytes = r.read()

        cached.write_bytes(img_bytes)
        return Response(content=img_bytes, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "No avatar available")


# ── Avatar debug ─────────────────────────────────────────────────────────────

@app.get("/api/avatar-debug/{user_id}")
def avatar_debug(user_id: int, authenticated_user: AuthenticatedUser):
    _require_owner(authenticated_user)
    token = config.BOT_TOKEN
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUserProfilePhotos",
        data=json.dumps({"user_id": user_id, "limit": 1}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


# ── Telegram auth ─────────────────────────────────────────────────────────────

class TelegramAuthRequest(BaseModel):
    init_data: str


@app.post("/api/auth/telegram")
def auth_telegram(req: TelegramAuthRequest):
    parsed = dict(urllib.parse.parse_qsl(req.init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(400, "Missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        raise HTTPException(403, "Invalid signature")

    try:
        auth_date = int(parsed.get("auth_date", 0))
    except ValueError as exc:
        raise HTTPException(400, "Invalid auth_date") from exc
    if auth_date <= 0 or abs(time.time() - auth_date) > 86400:
        raise HTTPException(403, "initData expired")

    try:
        user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid Telegram user data") from exc
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(400, "No user in initData")

    photo_url = user.get("photo_url")
    if photo_url:
        with db_conn() as db:
            db.execute(
                "UPDATE economy SET photo_url = ? WHERE user_id = ?",
                (photo_url, user_id),
            )
            db.commit()

    return {
        "user_id": user_id,
        "first_name": user.get("first_name", ""),
        "username": user.get("username", ""),
        "photo_url": photo_url,
        "session_token": _issue_session_token(user_id),
    }


@app.get("/api/auth/session")
def auth_session(authenticated_user: AuthenticatedUser):
    return {"user_id": authenticated_user}


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    with db_conn() as db:
        users = db.execute("SELECT COUNT(*) FROM economy").fetchone()[0]
        total_wrk = db.execute(
            "SELECT COALESCE(SUM(balance + secure_vault_balance "
            "+ vault_pending_amount), 0) FROM economy"
        ).fetchone()[0]
        gifts_owned = db.execute(
            "SELECT COUNT(*) FROM gift_instances WHERE owner_id IS NOT NULL"
        ).fetchone()[0]
        bank_gifts = db.execute(
            "SELECT COUNT(*) FROM gift_instances WHERE owner_id IS NULL"
        ).fetchone()[0]
        top_balance = db.execute(
            "SELECT COALESCE(MAX(balance), 0) FROM economy"
        ).fetchone()[0]
        games_played = db.execute(
            "SELECT COALESCE(SUM(slots_won+slots_lost+coinflip_won+coinflip_lost+"
            "blackjack_won+blackjack_lost+crash_won+crash_lost+duck_won+duck_lost+"
            "marbles_won+marbles_lost+livebj_won+livebj_lost+poker_won+poker_lost+"
            "roulette_won+roulette_lost+plinko_won+plinko_lost+wheel_won+wheel_lost+"
            "slider_won+slider_lost+craps_won+craps_lost+highlow_won+highlow_lost+"
            "cases_won+cases_lost),0) FROM game_stats"
        ).fetchone()[0]
        return {"users": users, "total_wrk": total_wrk, "gifts_owned": gifts_owned,
                "bank_gifts": bank_gifts, "top_balance": top_balance, "games_played": games_played}


# ── Games ─────────────────────────────────────────────────────────────────────

_SLOT_SYMBOLS = ["🍒", "🍋", "7️⃣", "💎", "🍀", "⭐"]

# ── Work / Jobs ───────────────────────────────────────────────────────────────

_JOBS = [
    (0,    "🧑‍🎓 Crypto Intern",    60,   120),
    (100,  "📈 Degen Trader",       120,  250),
    (300,  "🌾 Yield Farmer",       250,  500),
    (600,  "🔍 On-Chain Analyst",   400,  800),
    (1000, "⚙️ Protocol Dev",       600, 1200),
    (2000, "🦈 Blockchain Shark",   900, 1800),
    (5000, "👑 Blockchain Baron",  1000, 2000),
]
_SHIFT_MAX_TAPS = 50
_SHIFT_COOLDOWN = 15 * 60  # seconds


def _get_tier_index(work_count: int) -> int:
    idx = 0
    for i, (min_taps, *_) in enumerate(_JOBS):
        if work_count >= min_taps:
            idx = i
    return idx


def _job_payload(work_count: int) -> dict:
    idx = _get_tier_index(work_count)
    _, title, lo, hi = _JOBS[idx]
    next_job = None
    if idx + 1 < len(_JOBS):
        next_min, next_title, *_ = _JOBS[idx + 1]
        next_job = {"title": next_title, "taps_required": next_min, "taps_remaining": next_min - work_count}
    return {"title": title, "tier_index": idx, "earn_low": lo, "earn_high": hi, "next_job": next_job}


def _collect_shift(db, user_id: int, taps: int, earned: int) -> dict:
    """Delete active session, credit economy, return result dict."""
    db.execute("DELETE FROM work_sessions WHERE user_id = ?", (user_id,))
    now = int(time.time())
    cur = db.execute(
        "UPDATE economy SET balance = balance + ?, last_work = ?, work_count = work_count + ? WHERE user_id = ?",
        (earned, now, taps, user_id),
    )
    if cur.rowcount == 0:
        db.rollback()
        raise HTTPException(500, "Economy record missing")
    row = db.execute("SELECT balance, work_count FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    db.commit()
    new_work_count = row["work_count"] if row else 0
    new_balance = row["balance"] if row else 0
    old_tier = _get_tier_index(new_work_count - taps)
    new_tier = _get_tier_index(new_work_count)
    return {
        "collected": earned,
        "new_balance": new_balance,
        "taps": taps,
        "promoted": new_tier > old_tier,
        "new_job": _JOBS[new_tier][1] if new_tier > old_tier else None,
        "auto_ended": False,
    }


def _record_stats(db, user_id: int, *,
                  slots_won=0, slots_lost=0,
                  coinflip_won=0, coinflip_lost=0,
                  blackjack_won=0, blackjack_lost=0,
                  crash_won=0, crash_lost=0,
                  crash_mult=0.0,
                  duck_won=0, duck_lost=0,
                  marbles_won=0, marbles_lost=0,
                  livebj_won=0, livebj_lost=0,
                  poker_won=0, poker_lost=0,
                  roulette_won=0, roulette_lost=0,
                  plinko_won=0, plinko_lost=0,
                  wheel_won=0, wheel_lost=0,
                  slider_won=0, slider_lost=0,
                  craps_won=0, craps_lost=0,
                  highlow_won=0, highlow_lost=0,
                  cases_won=0, cases_lost=0) -> None:
    db.execute(
        """INSERT INTO game_stats
           (user_id,
            slots_won, slots_lost, coinflip_won, coinflip_lost,
            blackjack_won, blackjack_lost, crash_won, crash_lost, crash_best_mult,
            duck_won, duck_lost, marbles_won, marbles_lost,
            livebj_won, livebj_lost, poker_won, poker_lost,
            roulette_won, roulette_lost, plinko_won, plinko_lost,
            wheel_won, wheel_lost, slider_won, slider_lost,
            craps_won, craps_lost, highlow_won, highlow_lost,
            cases_won, cases_lost)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
               slots_won       = slots_won       + excluded.slots_won,
               slots_lost      = slots_lost      + excluded.slots_lost,
               coinflip_won    = coinflip_won    + excluded.coinflip_won,
               coinflip_lost   = coinflip_lost   + excluded.coinflip_lost,
               blackjack_won   = blackjack_won   + excluded.blackjack_won,
               blackjack_lost  = blackjack_lost  + excluded.blackjack_lost,
               crash_won       = crash_won       + excluded.crash_won,
               crash_lost      = crash_lost      + excluded.crash_lost,
               crash_best_mult = MAX(crash_best_mult, excluded.crash_best_mult),
               duck_won        = duck_won        + excluded.duck_won,
               duck_lost       = duck_lost       + excluded.duck_lost,
               marbles_won     = marbles_won     + excluded.marbles_won,
               marbles_lost    = marbles_lost    + excluded.marbles_lost,
               livebj_won      = livebj_won      + excluded.livebj_won,
               livebj_lost     = livebj_lost     + excluded.livebj_lost,
               poker_won       = poker_won       + excluded.poker_won,
               poker_lost      = poker_lost      + excluded.poker_lost,
               roulette_won    = roulette_won    + excluded.roulette_won,
               roulette_lost   = roulette_lost   + excluded.roulette_lost,
               plinko_won      = plinko_won      + excluded.plinko_won,
               plinko_lost     = plinko_lost     + excluded.plinko_lost,
               wheel_won       = wheel_won       + excluded.wheel_won,
               wheel_lost      = wheel_lost      + excluded.wheel_lost,
               slider_won      = slider_won      + excluded.slider_won,
               slider_lost     = slider_lost     + excluded.slider_lost,
               craps_won       = craps_won       + excluded.craps_won,
               craps_lost      = craps_lost      + excluded.craps_lost,
               highlow_won     = highlow_won     + excluded.highlow_won,
               highlow_lost    = highlow_lost    + excluded.highlow_lost,
               cases_won       = cases_won       + excluded.cases_won,
               cases_lost      = cases_lost      + excluded.cases_lost""",
        (user_id,
         slots_won, slots_lost, coinflip_won, coinflip_lost,
         blackjack_won, blackjack_lost, crash_won, crash_lost, crash_mult,
         duck_won, duck_lost, marbles_won, marbles_lost,
         livebj_won, livebj_lost, poker_won, poker_lost,
         roulette_won, roulette_lost, plinko_won, plinko_lost,
         wheel_won, wheel_lost, slider_won, slider_lost,
         craps_won, craps_lost, highlow_won, highlow_lost,
         cases_won, cases_lost),
    )
    db.commit()


def _slot_payout(reels: list[str]) -> tuple[str, int]:
    if reels == ["7️⃣", "7️⃣", "7️⃣"]:
        return "jackpot", 50
    if reels[0] == reels[1] == reels[2]:
        return "three_match", 12
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        return "two_match", 1  # push — bet returned, no profit
    return "no_match", 0


class BetRequest(BaseModel):
    user_id: int
    bet: int


class CoinflipRequest(BaseModel):
    user_id: int
    bet: int
    choice: str


class WorkStartRequest(BaseModel):
    user_id: int

class WorkSyncRequest(BaseModel):
    user_id: int
    taps_delta: int
    earned_delta: int

class WorkEndRequest(BaseModel):
    user_id: int


def _deduct_and_check(db, user_id: int, bet: int) -> int:
    row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "User not found — have you used the bot first?")
    if bet < 10:
        raise HTTPException(400, "Minimum bet is 10 WRK$")
    if row["balance"] < bet:
        raise HTTPException(400, f"Insufficient balance ({row['balance']:,} WRK$)")
    return row["balance"]


@app.post("/api/play/slots")
def play_slots(req: BetRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        reels = [random.choice(_SLOT_SYMBOLS) for _ in range(3)]
        kind, mult = _slot_payout(reels)
        delta = req.bet * (mult - 1) if mult > 0 else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        if delta > 0:
            _record_stats(db, req.user_id, slots_won=delta)
        elif delta < 0:
            _record_stats(db, req.user_id, slots_lost=req.bet)
        return {"reels": reels, "result": kind, "multiplier": mult,
                "delta": delta, "new_balance": new_bal}


@app.post("/api/play/coinflip")
def play_coinflip(req: CoinflipRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if req.choice not in ("heads", "tails"):
        raise HTTPException(400, "choice must be heads or tails")
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        result = random.choice(["heads", "tails"])
        won = result == req.choice
        delta = req.bet if won else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        if won:
            _record_stats(db, req.user_id, coinflip_won=req.bet)
        else:
            _record_stats(db, req.user_id, coinflip_lost=req.bet)
        return {"result": result, "won": won, "delta": delta, "new_balance": new_bal}


# ── Roulette ──────────────────────────────────────────────────────────────────

# American roulette wheel (clockwise from 0): (color_code, number). -1 = "00".
_RL_WHEEL = [
    ('G',0),('B',28),('R',9),('B',26),('R',30),('B',11),('R',7),('B',20),
    ('R',32),('B',17),('R',5),('B',22),('R',34),('B',15),('R',3),('B',24),
    ('R',36),('B',13),('R',1),('G',-1),('R',27),('B',10),('R',25),('B',29),
    ('R',12),('B',8),('R',19),('B',31),('R',18),('B',6),('R',21),('B',33),
    ('R',16),('B',4),('R',23),('B',35),('R',14),('B',2),
]

class RouletteRequest(BaseModel):
    user_id: int
    bet: int
    bet_type: str
    straight_number: int | None = None  # -1 represents 00


@app.post("/api/play/roulette")
def play_roulette(req: RouletteRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    valid = {
        "red", "black", "green", "odd", "even", "low", "high",
        "dozen1", "dozen2", "dozen3", "col1", "col2", "col3",
        "straight",
    }
    if req.bet_type not in valid:
        raise HTTPException(400, f"bet_type must be one of: {', '.join(sorted(valid))}")
    if req.bet_type == "straight" and (
        req.straight_number is None
        or req.straight_number < -1
        or req.straight_number > 36
    ):
        raise HTTPException(400, "straight_number must be 00, 0, or 1–36")
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        slot = random.randint(0, 37)
        color_code, number = _RL_WHEEL[slot]
        winning_color = {"G":"green","R":"red","B":"black"}[color_code]
        bt = req.bet_type
        if bt == "red":
            won, mult = color_code == "R", 2
        elif bt == "black":
            won, mult = color_code == "B", 2
        elif bt == "green":
            won, mult = color_code == "G", 14
        elif bt == "odd":
            won, mult = number > 0 and number % 2 == 1, 2
        elif bt == "even":
            won, mult = number > 0 and number % 2 == 0, 2
        elif bt == "low":
            won, mult = 1 <= number <= 18, 2
        elif bt == "high":
            won, mult = 19 <= number <= 36, 2
        elif bt == "dozen1":
            won, mult = 1 <= number <= 12, 3
        elif bt == "dozen2":
            won, mult = 13 <= number <= 24, 3
        elif bt == "dozen3":
            won, mult = 25 <= number <= 36, 3
        elif bt == "col1":
            won, mult = number > 0 and number % 3 == 1, 3
        elif bt == "col2":
            won, mult = number > 0 and number % 3 == 2, 3
        elif bt == "col3":
            won, mult = number > 0 and number % 3 == 0, 3
        else:  # straight
            won, mult = number == req.straight_number, 36
        delta = req.bet * (mult - 1) if won else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        if delta > 0:
            _record_stats(db, req.user_id, roulette_won=delta)
        elif delta < 0:
            _record_stats(db, req.user_id, roulette_lost=req.bet)
        db.commit()
        return {
            "slot": slot,
            "winning_color": winning_color,
            "winning_number": number if number >= 0 else "00",
            "won": won,
            "payout_mult": mult if won else 0,
            "delta": delta,
            "new_balance": new_bal,
        }


# ── High-Low Slider ───────────────────────────────────────────────────────────

class SliderRequest(BaseModel):
    user_id: int
    bet: int
    green_pct: int  # 5–95 inclusive; green zone width as percent
    flipped: bool = False


@app.post("/api/play/slider")
def play_slider(req: SliderRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if not (5 <= req.green_pct <= 95):
        raise HTTPException(400, "green_pct must be between 5 and 95")
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        payout_mult = round(min(19.0, 0.95 / (req.green_pct / 100)), 2)
        landing = random.randint(1, 100)
        threshold = 100 - req.green_pct  # boundary: <= threshold means arrow in left zone
        # NOT flipped: green on right, win = low landing (arrow flies right) = landing <= green_pct
        # Flipped:     green on left,  win = high landing (arrow flies left) = landing > threshold
        won = (landing > threshold) if req.flipped else (landing <= req.green_pct)
        delta = int(req.bet * (payout_mult - 1)) if won else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        if delta > 0:
            _record_stats(db, req.user_id, slider_won=delta)
        elif delta < 0:
            _record_stats(db, req.user_id, slider_lost=req.bet)
        db.commit()
        return {
            "won": won,
            "landing_pct": landing,
            "green_pct": req.green_pct,
            "payout_mult": payout_mult,
            "delta": delta,
            "new_balance": new_bal,
        }


# ── Plinko ────────────────────────────────────────────────────────────────────

_PLINKO_ROWS = 8
_PLINKO_MULTS = {
    "low":    [2.2, 1.5, 1.2, 0.9, 0.65, 0.9, 1.2, 1.5, 2.2],   # 94.5% RTP
    "medium": [7.0, 2.5, 1.4, 0.7, 0.50, 0.7, 1.4, 2.5, 7.0],   # 96.0% RTP
    "high":   [17,  3.5, 1.5, 0.5, 0.20, 0.5, 1.5, 3.5, 17 ],   # 95.3% RTP
}


class PlinkoRequest(BaseModel):
    user_id: int
    bet: int
    risk: str  # low | medium | high
    balls: int = 1


@app.post("/api/play/plinko")
def play_plinko(req: PlinkoRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if req.risk not in _PLINKO_MULTS:
        raise HTTPException(400, "risk must be low, medium, or high")
    if req.balls < 1 or req.balls > 10:
        raise HTTPException(400, "balls must be between 1 and 10")
    if req.bet < 10:
        raise HTTPException(400, "Minimum bet is 10 WRK$ per ball")
    total_bet = req.bet * req.balls
    with db_conn() as db:
        # Rapid one-ball drops arrive on separate request threads. Serialize the
        # wallet read/settlement so two presses cannot spend the same balance.
        db.execute("BEGIN IMMEDIATE")
        bal = _deduct_and_check(db, req.user_id, total_bet)
        drops = []
        won = 0
        lost = 0
        for _ in range(req.balls):
            path = [random.choice([False, True]) for _ in range(_PLINKO_ROWS)]
            slot = sum(1 for p in path if p)   # 0 = all-left, 8 = all-right
            mult = _PLINKO_MULTS[req.risk][slot]
            delta = int(req.bet * mult) - req.bet
            if delta > 0:
                won += delta
            elif delta < 0:
                lost += req.bet
            drops.append({
                "path": path,
                "slot": slot,
                "multiplier": mult,
                "delta": delta,
            })
        total_delta = sum(drop["delta"] for drop in drops)
        new_bal = bal + total_delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        if won or lost:
            _record_stats(db, req.user_id, plinko_won=won, plinko_lost=lost)
        db.commit()
        result = {
            "balls": req.balls,
            "bet_per_ball": req.bet,
            "total_bet": total_bet,
            "drops": drops,
            "total_delta": total_delta,
            "new_balance": new_bal,
        }
        # Keep the original one-ball response fields for older clients.
        result.update(drops[0])
        return result


# ── Wheel of Fortune ──────────────────────────────────────────────────────────

# 12 segments: 7 bankrupt, 3×1.5, 1×2.0, 1×5.0 → ~95.8% RTP
_WHEEL_SEGS = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 1.5, 1.5, 2.0, 5.0]


class WheelRequest(BaseModel):
    user_id: int
    bet: int


@app.post("/api/play/wheel")
def play_wheel(req: WheelRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        segment = random.randint(0, 11)
        mult = _WHEEL_SEGS[segment]
        delta = int(req.bet * mult) - req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        if delta > 0:
            _record_stats(db, req.user_id, wheel_won=delta)
        elif delta < 0:
            _record_stats(db, req.user_id, wheel_lost=req.bet)
        db.commit()
        return {
            "segment": segment,
            "multiplier": mult,
            "delta": delta,
            "new_balance": new_bal,
        }


# ── CS Case Opening ───────────────────────────────────────────────────────────

_CASE_PRICE = 55_000

# Cumulative weights (roll 1-100). Each entry: (threshold, tier, wrk_min, wrk_max, gift_tier)
_CASE_LOOT = [
    (55,  "common",    15_000,  40_000,  None),
    (80,  "uncommon",  40_000,  80_000,  None),
    (92,  "rare",      80_000, 200_000,  None),
    (98,  "epic",           0,       0,  "low"),
    (100, "legendary",      0,       0,  "mid"),
]


class CaseRequest(BaseModel):
    user_id: int


@app.post("/api/play/case")
def play_case(req: CaseRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    import time as _time
    with db_conn() as db:
        row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found — use the bot first")
        if row["balance"] < _CASE_PRICE:
            raise HTTPException(400, f"Need {_CASE_PRICE:,} WRK$ to open a case")
        balance = row["balance"] - _CASE_PRICE
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))

        roll = random.randint(1, 100)
        tier_name = wrk_min = wrk_max = gift_tier = None
        for threshold, t, mn, mx, gt in _CASE_LOOT:
            if roll <= threshold:
                tier_name, wrk_min, wrk_max, gift_tier = t, mn, mx, gt
                break

        gift_id = gift_name = gift_emoji = None
        wrk_reward = 0

        if gift_tier:
            instance = db.execute(
                "SELECT gi.id, gm.model_name, gm.model_emoji FROM gift_instances gi "
                "JOIN gift_models gm ON gm.id = gi.model_id "
                "WHERE gi.owner_id IS NULL AND gm.tier = ? ORDER BY RANDOM() LIMIT 1",
                (gift_tier,)
            ).fetchone()
            if instance:
                gift_id = instance["id"]
                gift_name = instance["model_name"]
                gift_emoji = instance["model_emoji"]
                db.execute("UPDATE gift_instances SET owner_id = ? WHERE id = ?", (req.user_id, gift_id))
            else:
                # Fallback if no gifts of this tier are in stock
                wrk_reward = 50_000 if gift_tier == "high" else 25_000
                balance += wrk_reward
                db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))
        else:
            wrk_reward = random.randint(wrk_min, wrk_max)
            balance += wrk_reward
            db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))

        db.execute(
            "INSERT INTO cases_opened (user_id, tier, wrk_reward, gift_id, opened_at) VALUES (?, ?, ?, ?, ?)",
            (req.user_id, tier_name, wrk_reward, gift_id, int(_time.time()))
        )
        if gift_id:
            gift_val_row = db.execute(
                "SELECT gp.base_price FROM gift_instances gi "
                "JOIN gift_models gm ON gm.id = gi.model_id "
                "JOIN gift_prices gp ON gp.collection = gm.collection "
                "WHERE gi.id = ?", (gift_id,)
            ).fetchone()
            cases_win_val = gift_val_row["base_price"] if gift_val_row else 50_000
        else:
            cases_win_val = wrk_reward
        net = cases_win_val - _CASE_PRICE
        if net >= 0:
            _record_stats(db, req.user_id, cases_won=net)
        else:
            _record_stats(db, req.user_id, cases_lost=-net)
        db.commit()
        return {
            "tier": tier_name,
            "wrk_reward": wrk_reward,
            "gift_id": gift_id,
            "gift_name": gift_name,
            "gift_emoji": gift_emoji,
            "new_balance": balance,
        }


# ── Street Craps ──────────────────────────────────────────────────────────────

class CrapsStartRequest(BaseModel):
    user_id: int
    bet: int


class CrapsRollRequest(BaseModel):
    user_id: int


@app.post("/api/play/craps/start")
def craps_start(req: CrapsStartRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        existing = db.execute(
            "SELECT user_id FROM craps_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if existing:
            raise HTTPException(400, "You already have an active craps session")
        bal = _deduct_and_check(db, req.user_id, req.bet)
        new_bal = bal - req.bet
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        now = int(time.time())
        db.execute(
            "INSERT INTO craps_sessions (user_id, bet, point, started_at) VALUES (?, ?, NULL, ?)",
            (req.user_id, req.bet, now),
        )
        db.commit()
        return {"session": {"user_id": req.user_id, "bet": req.bet, "point": None}, "new_balance": new_bal}


@app.post("/api/play/craps/roll")
def craps_roll(req: CrapsRollRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        sess = db.execute(
            "SELECT * FROM craps_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not sess:
            raise HTTPException(404, "No active craps session — start one first")
        sess = dict(sess)
        db.execute("UPDATE craps_sessions SET roll_count = roll_count + 1 WHERE user_id = ?", (req.user_id,))
        row = db.execute("SELECT roll_count FROM craps_sessions WHERE user_id = ?", (req.user_id,)).fetchone()
        roll_count = row["roll_count"] if row else 1
        d1 = random.randint(1, 6)
        d2 = random.randint(1, 6)
        total = d1 + d2

        if sess["point"] is None:
            if total in (7, 11):
                winnings = sess["bet"] * 2
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                new_bal = row["balance"] + winnings
                db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
                _record_stats(db, req.user_id, craps_won=winnings - sess["bet"])
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "win", "winnings": winnings, "new_balance": new_bal}
            elif total in (2, 3, 12):
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                _record_stats(db, req.user_id, craps_lost=sess["bet"])
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "lose", "lost": sess["bet"], "new_balance": row["balance"]}
            else:
                db.execute("UPDATE craps_sessions SET point = ? WHERE user_id = ?", (total, req.user_id))
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "point", "point": total}
        else:
            if total == sess["point"]:
                winnings = sess["bet"] * 2
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                new_bal = row["balance"] + winnings
                db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
                _record_stats(db, req.user_id, craps_won=winnings - sess["bet"])
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "win", "winnings": winnings, "new_balance": new_bal}
            elif total == 7:
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                _record_stats(db, req.user_id, craps_lost=sess["bet"])
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "lose", "lost": sess["bet"], "new_balance": row["balance"]}
            else:
                if roll_count >= 25:
                    refund = sess["bet"] // 2
                    db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                    row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                    new_bal = row["balance"] + refund
                    db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
                    db.commit()
                    return {"d1": d1, "d2": d2, "total": total, "result": "refund", "refund": refund, "new_balance": new_bal}
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "rolling", "point": sess["point"]}


@app.get("/api/play/craps/status/{user_id}")
def craps_status(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        sess = db.execute("SELECT * FROM craps_sessions WHERE user_id = ?", (user_id,)).fetchone()
        if not sess:
            return {"active": False}
        return {"active": True, **dict(sess)}


# ── Hack ──────────────────────────────────────────────────────────────────────

_WORDLIST = [
    ("chair",  "You sit on one."),
    ("bread",  "Baked from flour."),
    ("clock",  "Tells you the time."),
    ("light",  "Lets you see in the dark."),
    ("plant",  "Grows from soil."),
    ("storm",  "Wind and rain together."),
    ("flame",  "Fire's visible form."),
    ("glass",  "You drink from it."),
    ("stone",  "Hard piece of rock."),
    ("field",  "Open flat land."),
    ("cloud",  "Floats above your head."),
    ("brush",  "Used to paint or clean."),
    ("fence",  "A boundary between properties."),
    ("shelf",  "Holds things on a wall."),
    ("trail",  "A path through nature."),
    ("cabin",  "A small wooden house."),
    ("bloom",  "When a flower opens."),
    ("frost",  "Ice on cold surfaces."),
    ("creek",  "A small stream."),
    ("forge",  "Where metal gets shaped."),
    ("swamp",  "Wet, muddy ground."),
    ("ridge",  "Top of a long hill."),
    ("perch",  "Where a bird sits."),
    ("latch",  "Keeps a door shut."),
    ("ember",  "Glowing piece of coal."),
    ("prism",  "Splits light into colors."),
    ("grove",  "A small cluster of trees."),
    ("blaze",  "A bright, fast fire."),
    ("cloak",  "A long loose coat."),
    ("flute",  "A wind instrument."),
    ("graze",  "How cattle eat grass."),
    ("notch",  "A small cut in wood."),
]

def _hack_display(word: str, revealed: set) -> str:
    return " ".join(c if i in revealed else "_" for i, c in enumerate(word))

class HackStartRequest(BaseModel):
    user_id: int


class HackGuessRequest(BaseModel):
    user_id: int
    word: str


_HACK_COOLDOWN = 3600


@app.get("/api/hack/status/{user_id}")
def hack_status(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        row = db.execute("SELECT last_hack FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        now = int(time.time())
        cooldown_remaining = max(0, _HACK_COOLDOWN - (now - (row["last_hack"] or 0)))
        sess = db.execute("SELECT * FROM hack_sessions WHERE user_id = ?", (user_id,)).fetchone()
        if sess:
            sess = dict(sess)
            revealed = set(int(x) for x in sess["revealed_indices"].split(",") if x)
            display = _hack_display(sess["word"], revealed)
            return {
                "active": True,
                "display": display,
                "clue": sess["clue"],
                "attempts": sess["attempts"],
                "reward": sess["reward"],
                "word_length": len(sess["word"]),
                "cooldown_remaining": 0,
            }
        return {"active": False, "cooldown_remaining": cooldown_remaining}


@app.post("/api/hack/start")
def hack_start(req: HackStartRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        row = db.execute("SELECT last_hack, balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found — use the bot first")
        now = int(time.time())
        cooldown_remaining = max(0, _HACK_COOLDOWN - (now - (row["last_hack"] or 0)))
        if cooldown_remaining > 0:
            raise HTTPException(400, f"Hack on cooldown for {cooldown_remaining}s")
        existing = db.execute("SELECT user_id FROM hack_sessions WHERE user_id = ?", (req.user_id,)).fetchone()
        if existing:
            raise HTTPException(400, "You already have an active hack session")
        word, clue = random.choice(_WORDLIST)
        balance = row["balance"] or 0
        reward = random.randint(
            max(2_000, int(balance * 0.003)),
            max(10_000, min(int(balance * 0.008), 150_000)),
        )
        db.execute(
            "INSERT INTO hack_sessions (user_id, word, clue, reward, attempts, revealed_indices, started_at) "
            "VALUES (?, ?, ?, ?, 5, '0', ?)",
            (req.user_id, word, clue, reward, now),
        )
        db.commit()
        display = _hack_display(word, {0})
        return {"display": display, "clue": clue, "attempts": 5, "reward": reward, "word_length": len(word)}


@app.post("/api/hack/guess")
def hack_guess(req: HackGuessRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    guess = req.word.lower().strip()
    with db_conn() as db:
        sess = db.execute("SELECT * FROM hack_sessions WHERE user_id = ?", (req.user_id,)).fetchone()
        if not sess:
            raise HTTPException(404, "No active hack session")
        sess = dict(sess)
        word = sess["word"]
        revealed = set(int(x) for x in sess["revealed_indices"].split(",") if x)

        if guess == word:
            db.execute("DELETE FROM hack_sessions WHERE user_id = ?", (req.user_id,))
            db.execute("UPDATE economy SET last_hack = ? WHERE user_id = ?", (int(time.time()), req.user_id))
            row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
            if not row:
                raise HTTPException(500, "Economy record missing")
            new_bal = row["balance"] + sess["reward"]
            db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
            db.commit()
            return {"result": "win", "word": word, "reward": sess["reward"], "new_balance": new_bal}

        attempts_left = sess["attempts"] - 1
        if attempts_left <= 0:
            db.execute("DELETE FROM hack_sessions WHERE user_id = ?", (req.user_id,))
            db.execute("UPDATE economy SET last_hack = ? WHERE user_id = ?", (int(time.time()), req.user_id))
            db.commit()
            return {"result": "lose", "word": word, "attempts_left": 0}

        unrevealed = [i for i in range(len(word)) if i not in revealed]
        if unrevealed:
            revealed.add(random.choice(unrevealed))
        new_revealed_str = ",".join(str(i) for i in sorted(revealed))
        db.execute(
            "UPDATE hack_sessions SET attempts = ?, revealed_indices = ? WHERE user_id = ?",
            (attempts_left, new_revealed_str, req.user_id),
        )
        db.commit()
        display = _hack_display(word, revealed)
        return {"result": "wrong", "display": display, "attempts_left": attempts_left}


# ── Rob ───────────────────────────────────────────────────────────────────────

def _rob_outcome(success: bool, robber_balance: int, victim_balance: int) -> dict:
    if success:
        pct = random.uniform(0.03, 0.10)
        amount = max(1, int(victim_balance * pct))
        return {"outcome": "success", "amount": amount}
    r = random.random()
    if r < 0.60:
        amount = random.randint(50, 200)
        return {"outcome": "fine", "amount": amount}
    elif r < 0.90:
        amount = max(1, int(robber_balance * random.uniform(0.05, 0.15)))
        return {"outcome": "bail", "amount": amount}
    else:
        return {"outcome": "getaway", "amount": 0}

_ROB_SUCCESS = [
    ("🔫", "{robber} robbed {target} at gunpoint and walked away with {amount} WRK$!"),
    ("🌱", "{robber} was randomly guessing seed phrases and cracked {target}'s wallet for {amount} WRK$!"),
    ("📞", "{robber} was on a call and sneakily drained {target}'s wallet for {amount} WRK$!"),
    ("🎭", "{robber} pulled a classic social engineering play on {target} and got {amount} WRK$!"),
    ("🧢", "{robber} rug pulled {target} for {amount} WRK$. It was just a 'test token', bro."),
    ("🕵️", "{robber} deployed a honeypot contract and {target} fell for it. -{amount} WRK$!"),
    ("💌", "{robber} sent {target} a phishing link and drained {amount} WRK$ from their wallet!"),
    ("🔧", "{robber} exploited a zero-day in {target}'s opsec and extracted {amount} WRK$!"),
    ("🚗", "{robber} pulled up on {target}, took the bag, and peeled out with {amount} WRK$!"),
    ("🎯", "{robber} front-ran {target}'s transaction and sniped {amount} WRK$ in the mempool!"),
    ("🛸", "{robber} airdropped a malicious token into {target}'s wallet and drained {amount} WRK$!"),
    ("🏦", "{robber} bribed {target}'s validator and quietly skimmed {amount} WRK$!"),
    ("🧠", "{robber} talked {target} into a 'collab' and bounced with {amount} WRK$!"),
    ("💣", "{robber} flash-loaned their way into {target}'s liquidity pool and escaped with {amount} WRK$!"),
    ("😿", "{target} panic-listed their scared cat on MRKT under floor and {robber} scooped it for {amount} WRK$ profit!"),
]
_ROB_FINE = [
    ("🚔", "{robber} tried to rob {target} but got spooked and dropped {amount} WRK$ running away!"),
    ("👮", "{robber} got caught mid-heist on {target} and bribed the cop for {amount} WRK$!"),
    ("🐕", "{robber} set off {target}'s wallet alarm and tripped over their own getaway dog. Lost {amount} WRK$."),
    ("🧂", "{robber} fumbled the bag trying to rob {target} and scattered {amount} WRK$ on the floor."),
    ("🏃", "{robber} tried robbing {target} but {target}'s security was wild — lost {amount} WRK$ in the sprint!"),
    ("🪤", "{robber} walked into {target}'s honeypot trying to rob them. Ate a {amount} WRK$ fine."),
]
_ROB_BAIL = [
    ("🚨", "{robber} got arrested trying to rob {target}! Had to post {amount} WRK$ bail."),
    ("⛓️", "{robber} got cuffed outside {target}'s wallet. Lawyer fees: {amount} WRK$."),
    ("🏛️", "{robber} went to trial for robbing {target} and lost. Court fined them {amount} WRK$!"),
    ("📡", "{robber}'s heist on {target} was traced on-chain. Investigators froze {amount} WRK$."),
    ("🕵️", "{robber} got doxxed attempting to rob {target}. Restitution order: {amount} WRK$."),
]
_ROB_GETAWAY = [
    ("😮‍💨", "{robber} botched the rob on {target} but vanished into the crowd. No trace, no loss."),
    ("🌫️", "{robber} failed to crack {target}'s wallet but ghosted before anyone noticed."),
    ("🐱", "{robber} slipped away like a shadow after failing to hit {target}. Clean getaway."),
    ("🧊", "{robber} fumbled the job on {target} but kept their cool and disappeared. No loss."),
]

_ROB_COOLDOWN = 3600  # 1 hour


class RobAttemptRequest(BaseModel):
    user_id: int
    target_id: int


def _send_telegram_dm(user_id: int, text: str) -> None:
    token = config.BOT_TOKEN
    payload = json.dumps({"chat_id": user_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


@app.get("/api/rob/targets")
def rob_targets(user_id: int, authenticated_user: AuthenticatedUser, limit: int = 30):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        rows = db.execute(
            """SELECT e.user_id,
                      CASE
                        WHEN e.anon_mask_enabled = 1 AND security_anon.suffix IS NOT NULL
                        THEN printf('+888 %03d', security_anon.suffix)
                        ELSE COALESCE(a.full_name, e.full_name, 'User ' || e.user_id)
                      END AS name,
                      e.balance
               FROM economy e
               LEFT JOIN anon_numbers security_anon
                 ON security_anon.id = e.pinned_anon_id
                AND security_anon.owner_id = e.user_id
               LEFT JOIN (
                   SELECT user_id, full_name FROM user_activity
                   WHERE (user_id, last_seen) IN (
                       SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
                   )
               ) a ON a.user_id = e.user_id
               WHERE e.user_id != ? AND e.balance >= 500
               ORDER BY e.balance DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [{"user_id": r["user_id"], "name": r["name"], "balance": r["balance"]} for r in rows]


@app.post("/api/rob/attempt")
def rob_attempt(req: RobAttemptRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if req.user_id == req.target_id:
        raise HTTPException(400, "You can't rob yourself")
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        robber_row = db.execute(
            """SELECT e.balance, e.last_rob,
                      COALESCE(a.username, e.username) AS username,
                      COALESCE(a.full_name, e.full_name, 'User ' || e.user_id) AS full_name
               FROM economy e
               LEFT JOIN (
                   SELECT user_id, username, full_name FROM user_activity
                   WHERE (user_id, last_seen) IN (
                       SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
                   )
               ) a ON a.user_id = e.user_id
               WHERE e.user_id = ?""",
            (req.user_id,)
        ).fetchone()
        if not robber_row:
            raise HTTPException(404, "Robber not found")
        now = int(time.time())
        cooldown_remaining = max(0, _ROB_COOLDOWN - (now - (robber_row["last_rob"] or 0)))
        if cooldown_remaining > 0:
            raise HTTPException(400, f"Rob on cooldown for {cooldown_remaining}s")

        target_row = db.execute(
            """SELECT e.user_id, e.balance,
                      CASE
                        WHEN e.anon_mask_enabled = 1 AND security_anon.suffix IS NOT NULL
                        THEN printf('+888 %03d', security_anon.suffix)
                        ELSE COALESCE(a.full_name, e.full_name, 'User ' || e.user_id)
                      END AS name
               FROM economy e
               LEFT JOIN anon_numbers security_anon
                 ON security_anon.id = e.pinned_anon_id
                AND security_anon.owner_id = e.user_id
               LEFT JOIN (
                   SELECT user_id, full_name FROM user_activity
                   WHERE (user_id, last_seen) IN (
                       SELECT user_id, MAX(last_seen) FROM user_activity GROUP BY user_id
                   )
               ) a ON a.user_id = e.user_id
               WHERE e.user_id = ?""",
            (req.target_id,),
        ).fetchone()
        if not target_row or target_row["balance"] < 500:
            raise HTTPException(400, "Target doesn't have enough WRK$ (minimum 500)")

        db.execute("UPDATE economy SET last_rob = ? WHERE user_id = ?", (now, req.user_id))
        firewall = _consume_anon_firewall(
            db, req.target_id, req.user_id, now=now
        )
        target_name = target_row["name"]
        robber_display = _public_identity(db, req.user_id)
        if firewall["blocked"]:
            new_bal = robber_row["balance"]
            db.commit()
            _send_telegram_dm(
                req.target_id,
                f"🛡️ {firewall['number']} blocked a robbery attempt from "
                f"{robber_display}. Your WRK$ is safe.",
            )
            return {
                "outcome": "firewall",
                "emoji": "🛡️",
                "flavor": (
                    f"{target_name}'s +888 firewall intercepted the robbery. "
                    "No WRK$ moved."
                ),
                "amount": 0,
                "new_balance": new_bal,
                "firewall_remaining": firewall["remaining"],
            }

        success = random.random() < 0.50
        result = _rob_outcome(success, robber_row["balance"], target_row["balance"])

        if result["outcome"] == "success":
            amount = result["amount"]
            db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (amount, req.target_id))
            db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (amount, req.user_id))
            emoji, template = random.choice(_ROB_SUCCESS)
            flavor = template.format(robber="You", target=target_name, amount=f"{amount:,}")
            _send_telegram_dm(req.target_id, f"{emoji} {robber_display} robbed you and stole {amount:,} WRK$ from your wallet!")
        elif result["outcome"] == "fine":
            amount = result["amount"]
            db.execute("UPDATE economy SET balance = MAX(0, balance - ?) WHERE user_id = ?", (amount, req.user_id))
            emoji, template = random.choice(_ROB_FINE)
            flavor = template.format(robber="You", target=target_name, amount=f"{amount:,}")
        elif result["outcome"] == "bail":
            amount = result["amount"]
            db.execute("UPDATE economy SET balance = MAX(0, balance - ?) WHERE user_id = ?", (amount, req.user_id))
            emoji, template = random.choice(_ROB_BAIL)
            flavor = template.format(robber="You", target=target_name, amount=f"{amount:,}")
        else:
            amount = 0
            emoji, template = random.choice(_ROB_GETAWAY)
            flavor = template.format(robber="You", target=target_name, amount="0")

        new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()["balance"]
        db.commit()
        return {"outcome": result["outcome"], "emoji": emoji, "flavor": flavor, "amount": amount, "new_balance": new_bal}


@app.get("/api/rob/cooldown/{user_id}")
def rob_cooldown_status(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        row = db.execute("SELECT last_rob FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        now = int(time.time())
        remaining = max(0, _ROB_COOLDOWN - (now - (row["last_rob"] or 0)))
        return {"cooldown_remaining": remaining}


# ── Work / Jobs endpoints ─────────────────────────────────────────────────────

@app.get("/api/work/status/{user_id}")
def work_status(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        row = db.execute(
            "SELECT work_count, last_work FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        now = int(time.time())
        cooldown_remaining = max(0, _SHIFT_COOLDOWN - (now - (row["last_work"] or 0)))
        work_count = row["work_count"] or 0
        session_row = db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
        job = _job_payload(work_count)
        return {
            "session": dict(session_row) if session_row else None,
            "cooldown_remaining": cooldown_remaining,
            "job": {k: v for k, v in job.items() if k != "next_job"},
            "next_job": job["next_job"],
            "lifetime_taps": work_count,
        }


@app.post("/api/work/start")
def work_start(req: WorkStartRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        row = db.execute(
            "SELECT work_count, last_work FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found — use the bot first")
        now = int(time.time())
        cooldown_remaining = max(0, _SHIFT_COOLDOWN - (now - (row["last_work"] or 0)))
        if cooldown_remaining > 0:
            raise HTTPException(400, f"Shift on cooldown for {cooldown_remaining}s")
        existing = db.execute(
            "SELECT user_id FROM work_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if existing:
            raise HTTPException(400, "Shift already active")
        work_count = row["work_count"] or 0
        tier_index = _get_tier_index(work_count)
        db.execute(
            "INSERT INTO work_sessions (user_id, taps, earned, started_at, job_tier_index, tap_count_start) "
            "VALUES (?, 0, 0, ?, ?, ?)",
            (req.user_id, now, tier_index, work_count),
        )
        db.commit()
        job = _job_payload(work_count)
        return {
            "session": {"user_id": req.user_id, "taps": 0, "earned": 0,
                        "started_at": now, "job_tier_index": tier_index, "tap_count_start": work_count},
            "cooldown_remaining": 0,
            "job": {k: v for k, v in job.items() if k != "next_job"},
            "next_job": job["next_job"],
            "lifetime_taps": work_count,
        }


@app.post("/api/work/sync")
def work_sync(req: WorkSyncRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if req.taps_delta < 1 or req.taps_delta > _SHIFT_MAX_TAPS:
        raise HTTPException(400, "taps_delta out of range")
    with db_conn() as db:
        session_row = db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not session_row:
            raise HTTPException(404, "No active shift")
        session = dict(session_row)
        _, _, lo, hi = _JOBS[session["job_tier_index"]]
        max_plausible = req.taps_delta * hi * 1.1
        if req.earned_delta > max_plausible or req.earned_delta < 0:
            raise HTTPException(400, "Earnings out of plausible range")
        new_taps = session["taps"] + req.taps_delta
        new_earned = session["earned"] + req.earned_delta
        if new_taps > _SHIFT_MAX_TAPS:
            raise HTTPException(400, f"Would exceed max taps ({_SHIFT_MAX_TAPS})")
        if new_taps >= _SHIFT_MAX_TAPS:
            # Skip intermediate commit — _collect_shift handles deletion + credit atomically
            result = _collect_shift(db, req.user_id, new_taps, new_earned)
            result["auto_ended"] = True
            return result
        db.execute(
            "UPDATE work_sessions SET taps = ?, earned = ? WHERE user_id = ?",
            (new_taps, new_earned, req.user_id),
        )
        db.commit()
        return {
            "session": {**session, "taps": new_taps, "earned": new_earned},
            "auto_ended": False,
        }


@app.post("/api/work/end")
def work_end(req: WorkEndRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        session_row = db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not session_row:
            raise HTTPException(404, "No active shift")
        session = dict(session_row)
        return _collect_shift(db, req.user_id, session["taps"], session["earned"])


# ── Market ────────────────────────────────────────────────────────────────────

@app.get("/api/market/collections")
def market_collections(tier: str = "low"):
    with db_conn() as db:
        if tier == "all":
            rows = db.execute(
                """SELECT DISTINCT gm.collection FROM gift_instances gi
                   JOIN gift_models gm ON gm.id = gi.model_id
                   WHERE gi.owner_id IS NULL
                     AND COALESCE(gi.is_admin_gift, 0) = 0
                   ORDER BY gm.collection"""
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT DISTINCT gm.collection FROM gift_instances gi
                   JOIN gift_models gm ON gm.id = gi.model_id
                   WHERE gi.owner_id IS NULL
                     AND COALESCE(gi.is_admin_gift, 0) = 0
                     AND gm.tier = ?
                   ORDER BY gm.collection""",
                (tier,),
            ).fetchall()
    return [r["collection"] for r in rows]


@app.get("/api/market")
def market_listings(tier: str = "low", limit: int = 40, offset: int = 0,
                    search: str = "", background: str = "", collection: str = ""):
    valid_tiers = ("low", "mid", "high", "all")
    if tier not in valid_tiers:
        raise HTTPException(400, "tier must be low | mid | high | all")
    where = [
        "gi.owner_id IS NULL",
        "COALESCE(gi.is_admin_gift, 0) = 0",
    ]
    params: list = []
    if tier != "all":
        where.append("gm.tier = ?")
        params.append(tier)
    if search:
        if search.isdigit():
            where.append("(gm.model_name LIKE ? OR gm.collection LIKE ? OR gi.gift_number = ?)")
            params += [f"%{search}%", f"%{search}%", int(search)]
        else:
            where.append("(gm.model_name LIKE ? OR gm.collection LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
    if background:
        where.append("gi.background = ?")
        params.append(background)
    if collection:
        where.append("gm.collection = ?")
        params.append(collection)
    where_sql = " AND ".join(where)
    with db_conn() as db:
        rows = db.execute(
            f"""SELECT gm.collection, gm.model_number, gm.model_name, gm.tier,
                       gm.custom_emoji_id, gi.background, COUNT(gi.id) AS stock, gp.current_price,
                       MIN(gi.gift_number) AS min_gift_number
                FROM gift_instances gi
                JOIN gift_models gm ON gm.id = gi.model_id
                JOIN gift_prices gp ON gp.collection = gm.collection AND gp.background = gi.background
                WHERE {where_sql}
                GROUP BY gm.collection, gm.model_number, gi.background
                ORDER BY gp.current_price ASC, gm.collection, gm.model_number
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        total = db.execute(
            f"""SELECT COUNT(*) FROM (
                SELECT 1 FROM gift_instances gi
                JOIN gift_models gm ON gm.id = gi.model_id
                WHERE {where_sql}
                GROUP BY gm.collection, gm.model_number, gi.background)""",
            params,
        ).fetchone()[0]
    return {"items": [dict(r) for r in rows], "total": total, "offset": offset}


class MarketBuyRequest(BaseModel):
    user_id: int
    collection: str
    model_number: int
    background: str


@app.post("/api/market/buy")
def market_buy(req: MarketBuyRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        row = db.execute(
            """SELECT gi.id, gi.gift_number FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.owner_id IS NULL
                 AND COALESCE(gi.is_admin_gift, 0) = 0
                 AND gm.collection = ? AND gm.model_number = ? AND gi.background = ?
               ORDER BY gi.gift_number ASC LIMIT 1""",
            (req.collection, req.model_number, req.background),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Out of stock")
        instance_id, gift_number = row["id"], row["gift_number"]

        price_row = db.execute(
            "SELECT current_price FROM gift_prices WHERE collection = ? AND background = ?",
            (req.collection, req.background),
        ).fetchone()
        if not price_row:
            raise HTTPException(500, "No price data")
        price = price_row["current_price"]

        user_row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
        if not user_row:
            raise HTTPException(404, "User not found — use the bot first")
        if user_row["balance"] < price:
            raise HTTPException(400, f"Insufficient balance ({user_row['balance']:,} WRK$)")

        db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (price, req.user_id))
        db.execute("UPDATE gift_instances SET owner_id = ? WHERE id = ?", (req.user_id, instance_id))
        db.execute(
            "UPDATE gift_prices SET demand_pressure = demand_pressure + 1 WHERE collection = ? AND background = ?",
            (req.collection, req.background),
        )
        new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()["balance"]
        db.commit()

    return {"gift_number": gift_number, "price": price, "new_balance": new_bal}


# ── Shop hub: Rift buyback, MKRT listings, Fragsmint, wallet ─────────────────

class RiftSellRequest(BaseModel):
    user_id: int
    gift_id: int


class MkrtListRequest(BaseModel):
    user_id: int
    gift_id: int
    price: int


class ShopActorRequest(BaseModel):
    user_id: int


class AnonBuyRequest(BaseModel):
    user_id: int
    anon_id: int


class AnonPinRequest(BaseModel):
    user_id: int
    anon_id: int | None = None


class SecurityAmountRequest(BaseModel):
    user_id: int
    amount: int


class SecurityActorRequest(BaseModel):
    user_id: int


class SecurityMaskRequest(BaseModel):
    user_id: int
    enabled: bool


def _anon_item(row) -> dict:
    item = dict(row)
    item["number"] = format_anon_number(item["suffix"])
    item["rarity"] = anon_number_rarity(item["suffix"])[0]
    return item


@app.get("/api/wallet")
def shop_wallet(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        wallet = db.execute(
            "SELECT balance, pinned_gift_id, pinned_anon_id FROM economy WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not wallet:
            raise HTTPException(404, "User not found — use the bot first")
        gifts = db.execute(
            """SELECT gi.id, gi.gift_number, gi.background, gi.acquired_at,
                      COALESCE(gi.staked, 0) AS staked,
                      COALESCE(gi.is_admin_gift, 0) AS is_admin_gift,
                      gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                      gm.tier, gm.custom_emoji_id, COALESCE(gp.current_price, 0) AS current_price,
                      l.id AS listing_id, l.price AS listing_price
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               LEFT JOIN gift_prices gp
                      ON gp.collection = gm.collection AND gp.background = gi.background
               LEFT JOIN gift_market_listings l
                      ON l.gift_id = gi.id AND l.status = 'active'
               WHERE gi.owner_id = ?
               ORDER BY COALESCE(gi.sort_index, 999999), gi.acquired_at DESC""",
            (user_id,),
        ).fetchall()
        anons = db.execute(
            "SELECT id, suffix, price, acquired_at FROM anon_numbers "
            "WHERE owner_id = ? ORDER BY suffix",
            (user_id,),
        ).fetchall()
        security = _security_status(db, user_id)

    gift_items = []
    for row in gifts:
        gift = dict(row)
        gift["buyback_price"] = int(gift["current_price"] * 0.80)
        gift["is_pinned"] = gift["id"] == wallet["pinned_gift_id"]
        gift_items.append(gift)
    anon_items = [_anon_item(row) for row in anons]
    for item in anon_items:
        item["is_pinned"] = item["id"] == wallet["pinned_anon_id"]
    return {
        "balance": wallet["balance"],
        "gifts": gift_items,
        "anon_numbers": anon_items,
        "pinned_anon_id": wallet["pinned_anon_id"],
        "security": security,
    }


@app.get("/api/security")
def security_status(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        return _security_status(db, user_id, event_limit=20)


@app.post("/api/security/vault/deposit")
def security_vault_deposit(
    req: SecurityAmountRequest,
    authenticated_user: AuthenticatedUser,
):
    _require_actor(authenticated_user, req.user_id)
    if req.amount < 100:
        raise HTTPException(400, "Minimum vault deposit is 100 WRK$")
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _active_security_number(db, req.user_id):
            raise HTTPException(400, "Activate an Anonymous Number first")
        row = db.execute(
            "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not row or row["balance"] < req.amount:
            raise HTTPException(400, "Insufficient spendable balance")
        db.execute(
            "UPDATE economy SET balance = balance - ?, "
            "secure_vault_balance = secure_vault_balance + ? WHERE user_id = ?",
            (req.amount, req.amount, req.user_id),
        )
        _record_security_event(
            db,
            req.user_id,
            "vault_deposit",
            "Deposited WRK$ into Secure Vault",
            amount=req.amount,
        )
        result = _security_status(db, req.user_id)
        db.commit()
        return result


@app.post("/api/security/vault/withdraw")
def security_vault_withdraw(
    req: SecurityAmountRequest,
    authenticated_user: AuthenticatedUser,
):
    _require_actor(authenticated_user, req.user_id)
    if req.amount < 100:
        raise HTTPException(400, "Minimum withdrawal is 100 WRK$")
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _active_security_number(db, req.user_id):
            raise HTTPException(400, "Activate an Anonymous Number first")
        row = db.execute(
            "SELECT secure_vault_balance, vault_pending_amount FROM economy "
            "WHERE user_id = ?",
            (req.user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        if row["vault_pending_amount"] > 0:
            raise HTTPException(400, "A vault withdrawal is already pending")
        if row["secure_vault_balance"] < req.amount:
            raise HTTPException(400, "Insufficient Secure Vault balance")
        available_at = int(time.time()) + ANON_VAULT_WITHDRAW_DELAY
        db.execute(
            "UPDATE economy SET secure_vault_balance = secure_vault_balance - ?, "
            "vault_pending_amount = ?, vault_withdraw_available_at = ? "
            "WHERE user_id = ?",
            (req.amount, req.amount, available_at, req.user_id),
        )
        _record_security_event(
            db,
            req.user_id,
            "withdraw_requested",
            "Started the 24-hour vault withdrawal lock",
            amount=req.amount,
        )
        result = _security_status(db, req.user_id)
        db.commit()
        return result


@app.post("/api/security/vault/claim")
def security_vault_claim(
    req: SecurityActorRequest,
    authenticated_user: AuthenticatedUser,
):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _active_security_number(db, req.user_id):
            raise HTTPException(400, "Activate an Anonymous Number first")
        row = db.execute(
            "SELECT vault_pending_amount, vault_withdraw_available_at "
            "FROM economy WHERE user_id = ?",
            (req.user_id,),
        ).fetchone()
        if not row or row["vault_pending_amount"] <= 0:
            raise HTTPException(400, "No pending vault withdrawal")
        now = int(time.time())
        if now < row["vault_withdraw_available_at"]:
            remaining = row["vault_withdraw_available_at"] - now
            raise HTTPException(400, f"Vault remains locked for {remaining}s")
        amount = row["vault_pending_amount"]
        db.execute(
            "UPDATE economy SET balance = balance + ?, vault_pending_amount = 0, "
            "vault_withdraw_available_at = 0 WHERE user_id = ?",
            (amount, req.user_id),
        )
        _record_security_event(
            db,
            req.user_id,
            "withdraw_claimed",
            "Claimed a matured vault withdrawal",
            amount=amount,
        )
        result = _security_status(db, req.user_id)
        db.commit()
        return result


@app.post("/api/security/vault/cancel")
def security_vault_cancel(
    req: SecurityActorRequest,
    authenticated_user: AuthenticatedUser,
):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT vault_pending_amount FROM economy WHERE user_id = ?",
            (req.user_id,),
        ).fetchone()
        if not row or row["vault_pending_amount"] <= 0:
            raise HTTPException(400, "No pending vault withdrawal")
        amount = row["vault_pending_amount"]
        db.execute(
            "UPDATE economy SET secure_vault_balance = secure_vault_balance + ?, "
            "vault_pending_amount = 0, vault_withdraw_available_at = 0 "
            "WHERE user_id = ?",
            (amount, req.user_id),
        )
        _record_security_event(
            db,
            req.user_id,
            "withdraw_cancelled",
            "Cancelled a pending vault withdrawal",
            amount=amount,
        )
        result = _security_status(db, req.user_id)
        db.commit()
        return result


@app.post("/api/security/mask")
def security_mask(
    req: SecurityMaskRequest,
    authenticated_user: AuthenticatedUser,
):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        active = _active_security_number(db, req.user_id)
        if req.enabled and not active:
            raise HTTPException(400, "Activate an Anonymous Number first")
        db.execute(
            "UPDATE economy SET anon_mask_enabled = ? WHERE user_id = ?",
            (1 if req.enabled else 0, req.user_id),
        )
        _record_security_event(
            db,
            req.user_id,
            "identity_mask",
            "Enabled +888 public identity"
            if req.enabled
            else "Disabled +888 public identity",
        )
        result = _security_status(db, req.user_id)
        db.commit()
        return result


@app.post("/api/rift/sell")
def rift_sell(req: RiftSellRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        gift = db.execute(
            """SELECT gi.id, gi.gift_number, gi.background, COALESCE(gi.staked, 0) AS staked,
                      COALESCE(gi.is_admin_gift, 0) AS is_admin_gift,
                      gm.collection, gm.model_name,
                      COALESCE(gp.current_price, 0) AS current_price,
                      EXISTS(
                          SELECT 1 FROM gift_market_listings l
                          WHERE l.gift_id = gi.id AND l.status = 'active'
                      ) AS is_listed
               FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               LEFT JOIN gift_prices gp
                      ON gp.collection = gm.collection AND gp.background = gi.background
               WHERE gi.id = ? AND gi.owner_id = ?""",
            (req.gift_id, req.user_id),
        ).fetchone()
        if not gift:
            raise HTTPException(404, "You do not own that gift")
        if gift["is_admin_gift"]:
            raise HTTPException(400, "Admin gifts cannot be sold")
        if gift["staked"]:
            raise HTTPException(400, "Unstake this gift before selling it")
        if gift["is_listed"]:
            raise HTTPException(400, "Cancel the MKRT listing before selling to Rift")
        buyback = int(gift["current_price"] * 0.80)
        if buyback <= 0:
            raise HTTPException(400, "This gift does not have a Rift buyback price")

        db.execute(
            "UPDATE gift_instances SET owner_id = NULL, acquired_at = NULL WHERE id = ?",
            (req.gift_id,),
        )
        db.execute(
            "UPDATE economy SET balance = balance + ?, "
            "pinned_gift_id = CASE WHEN pinned_gift_id = ? THEN NULL ELSE pinned_gift_id END "
            "WHERE user_id = ?",
            (buyback, req.gift_id, req.user_id),
        )
        db.execute(
            "UPDATE gift_prices SET demand_pressure = demand_pressure - 1 "
            "WHERE collection = ? AND background = ?",
            (gift["collection"], gift["background"]),
        )
        new_balance = db.execute(
            "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()["balance"]
        db.commit()
    return {
        "gift_id": req.gift_id,
        "gift_number": gift["gift_number"],
        "buyback_price": buyback,
        "new_balance": new_balance,
    }


@app.get("/api/mkrt")
def mkrt_listings(limit: int = 40, offset: int = 0, search: str = ""):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    where = [
        "l.status = 'active'",
        "gi.owner_id = l.seller_id",
        "COALESCE(gi.is_admin_gift, 0) = 0",
    ]
    params: list = []
    if search:
        if search.isdigit():
            where.append(
                "(gm.model_name LIKE ? OR gm.collection LIKE ? OR gi.gift_number = ?)"
            )
            params.extend([f"%{search}%", f"%{search}%", int(search)])
        else:
            where.append("(gm.model_name LIKE ? OR gm.collection LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
    where_sql = " AND ".join(where)
    with db_conn() as db:
        rows = db.execute(
            f"""SELECT l.id AS listing_id, l.seller_id, l.price, l.created_at,
                       gi.id AS gift_id, gi.gift_number, gi.background,
                       gm.collection, gm.model_number, gm.model_name, gm.model_emoji,
                       gm.tier, gm.custom_emoji_id,
                       e.username AS seller_username, e.full_name AS seller_full_name,
                       e.anon_mask_enabled AS seller_mask_enabled,
                       security_anon.suffix AS seller_mask_suffix
                FROM gift_market_listings l
                JOIN gift_instances gi ON gi.id = l.gift_id
                JOIN gift_models gm ON gm.id = gi.model_id
                LEFT JOIN economy e ON e.user_id = l.seller_id
                LEFT JOIN anon_numbers security_anon
                  ON security_anon.id = e.pinned_anon_id
                 AND security_anon.owner_id = e.user_id
                WHERE {where_sql}
                ORDER BY l.created_at DESC, l.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        total = db.execute(
            f"""SELECT COUNT(*) FROM gift_market_listings l
                JOIN gift_instances gi ON gi.id = l.gift_id
                JOIN gift_models gm ON gm.id = gi.model_id
                WHERE {where_sql}""",
            params,
        ).fetchone()[0]

    items = []
    for row in rows:
        item = dict(row)
        item["seller_name"] = (
            format_anon_number(item["seller_mask_suffix"])
            if item["seller_mask_enabled"] and item["seller_mask_suffix"] is not None
            else f"@{item['seller_username']}" if item["seller_username"]
            else item["seller_full_name"] or f"User {item['seller_id']}"
        )
        items.append(item)
    return {"items": items, "total": total, "offset": offset}


@app.post("/api/mkrt/list")
def mkrt_create_listing(req: MkrtListRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if req.price < 10:
        raise HTTPException(400, "MKRT price must be at least 10 WRK$")
    if req.price > 1_000_000_000_000_000:
        raise HTTPException(400, "MKRT price is too large")
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        gift = db.execute(
            "SELECT id, COALESCE(staked, 0) AS staked, "
            "COALESCE(is_admin_gift, 0) AS is_admin_gift FROM gift_instances "
            "WHERE id = ? AND owner_id = ?",
            (req.gift_id, req.user_id),
        ).fetchone()
        if not gift:
            raise HTTPException(404, "You do not own that gift")
        if gift["is_admin_gift"]:
            raise HTTPException(400, "Admin gifts cannot be listed")
        if gift["staked"]:
            raise HTTPException(400, "Unstake this gift before listing it")
        existing = db.execute(
            "SELECT id FROM gift_market_listings WHERE gift_id = ? AND status = 'active'",
            (req.gift_id,),
        ).fetchone()
        if existing:
            raise HTTPException(400, "That gift is already listed on MKRT")
        cur = db.execute(
            "INSERT INTO gift_market_listings "
            "(gift_id, seller_id, price, status, created_at) VALUES (?, ?, ?, 'active', ?)",
            (req.gift_id, req.user_id, req.price, int(time.time())),
        )
        listing_id = cur.lastrowid
        db.commit()
    return {"listing_id": listing_id, "price": req.price}


@app.post("/api/mkrt/{listing_id}/cancel")
def mkrt_cancel_listing(
    listing_id: int,
    req: ShopActorRequest,
    authenticated_user: AuthenticatedUser,
):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        cur = db.execute(
            "UPDATE gift_market_listings SET status = 'cancelled' "
            "WHERE id = ? AND seller_id = ? AND status = 'active'",
            (listing_id, req.user_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(404, "Active listing not found")
        db.commit()
    return {"ok": True}


@app.post("/api/mkrt/{listing_id}/buy")
def mkrt_buy_listing(
    listing_id: int,
    req: ShopActorRequest,
    authenticated_user: AuthenticatedUser,
):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        listing = db.execute(
            """SELECT l.id, l.gift_id, l.seller_id, l.price, l.status,
                      gi.owner_id, gi.gift_number,
                      COALESCE(gi.is_admin_gift, 0) AS is_admin_gift
               FROM gift_market_listings l
               JOIN gift_instances gi ON gi.id = l.gift_id
               WHERE l.id = ?""",
            (listing_id,),
        ).fetchone()
        if not listing or listing["status"] != "active":
            raise HTTPException(404, "This MKRT listing is no longer active")
        if listing["is_admin_gift"]:
            db.execute(
                "UPDATE gift_market_listings SET status = 'cancelled' WHERE id = ?",
                (listing_id,),
            )
            db.commit()
            raise HTTPException(400, "Admin gifts cannot be traded")
        if listing["owner_id"] != listing["seller_id"]:
            db.execute(
                "UPDATE gift_market_listings SET status = 'cancelled' WHERE id = ?",
                (listing_id,),
            )
            db.commit()
            raise HTTPException(409, "The seller no longer owns this gift")
        if listing["seller_id"] == req.user_id:
            raise HTTPException(400, "You cannot buy your own listing")
        buyer = db.execute(
            "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not buyer:
            raise HTTPException(404, "User not found — use the bot first")
        if buyer["balance"] < listing["price"]:
            raise HTTPException(400, f"Insufficient balance ({buyer['balance']:,} WRK$)")

        sold_at = int(time.time())
        db.execute(
            "UPDATE economy SET balance = balance - ? WHERE user_id = ?",
            (listing["price"], req.user_id),
        )
        db.execute(
            "UPDATE economy SET balance = balance + ?, "
            "pinned_gift_id = CASE WHEN pinned_gift_id = ? THEN NULL ELSE pinned_gift_id END "
            "WHERE user_id = ?",
            (listing["price"], listing["gift_id"], listing["seller_id"]),
        )
        db.execute(
            "UPDATE gift_instances SET owner_id = ?, acquired_at = ? WHERE id = ?",
            (req.user_id, sold_at, listing["gift_id"]),
        )
        db.execute(
            "UPDATE gift_market_listings SET status = 'sold', buyer_id = ?, sold_at = ? "
            "WHERE id = ?",
            (req.user_id, sold_at, listing_id),
        )
        new_balance = db.execute(
            "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()["balance"]
        db.commit()
    return {
        "gift_id": listing["gift_id"],
        "gift_number": listing["gift_number"],
        "price": listing["price"],
        "new_balance": new_balance,
    }


@app.get("/api/fragsmint")
def fragsmint_numbers(limit: int = 40, offset: int = 0, search: str = ""):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    where = ["owner_id IS NULL"]
    params: list = []
    clean_search = search.replace("+", "").replace(" ", "")
    if clean_search.startswith("888"):
        clean_search = clean_search[3:]
    if clean_search:
        if not clean_search.isdigit() or len(clean_search) > 3:
            return {"items": [], "total": 0, "offset": offset}
        where.append("printf('%03d', suffix) LIKE ?")
        params.append(f"%{clean_search}%")
    where_sql = " AND ".join(where)
    with db_conn() as db:
        rows = db.execute(
            f"SELECT id, suffix, price FROM anon_numbers WHERE {where_sql} "
            "ORDER BY price ASC, suffix ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        total = db.execute(
            f"SELECT COUNT(*) FROM anon_numbers WHERE {where_sql}", params
        ).fetchone()[0]
    return {
        "items": [_anon_item(row) for row in rows],
        "total": total,
        "offset": offset,
    }


@app.post("/api/fragsmint/buy")
def fragsmint_buy(req: AnonBuyRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        anon = db.execute(
            "SELECT id, suffix, price, owner_id FROM anon_numbers WHERE id = ?",
            (req.anon_id,),
        ).fetchone()
        if not anon:
            raise HTTPException(404, "Anonymous number not found")
        if anon["owner_id"] is not None:
            raise HTTPException(409, "That anonymous number is already owned")
        buyer = db.execute(
            "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not buyer:
            raise HTTPException(404, "User not found — use the bot first")
        if buyer["balance"] < anon["price"]:
            raise HTTPException(400, f"Insufficient balance ({buyer['balance']:,} WRK$)")
        acquired_at = int(time.time())
        db.execute(
            "UPDATE economy SET balance = balance - ? WHERE user_id = ?",
            (anon["price"], req.user_id),
        )
        db.execute(
            "UPDATE anon_numbers SET owner_id = ?, acquired_at = ? WHERE id = ?",
            (req.user_id, acquired_at, req.anon_id),
        )
        new_balance = db.execute(
            "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()["balance"]
        db.commit()
    return {
        "anon_id": anon["id"],
        "number": format_anon_number(anon["suffix"]),
        "price": anon["price"],
        "new_balance": new_balance,
    }


@app.post("/api/profile/pin-anon")
def pin_anon_number(req: AnonPinRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        if req.anon_id is not None:
            owned = db.execute(
                "SELECT id, suffix FROM anon_numbers WHERE id = ? AND owner_id = ?",
                (req.anon_id, req.user_id),
            ).fetchone()
            if not owned:
                raise HTTPException(403, "You do not own that anonymous number")
        else:
            secured = db.execute(
                "SELECT secure_vault_balance, vault_pending_amount "
                "FROM economy WHERE user_id = ?",
                (req.user_id,),
            ).fetchone()
            if secured and (
                secured["secure_vault_balance"] > 0
                or secured["vault_pending_amount"] > 0
            ):
                raise HTTPException(
                    400,
                    "Move all funds out of Secure Vault before deactivating your number",
                )
        db.execute(
            "UPDATE economy SET pinned_anon_id = ?, "
            "anon_mask_enabled = CASE WHEN ? IS NULL THEN 0 ELSE anon_mask_enabled END "
            "WHERE user_id = ?",
            (req.anon_id, req.anon_id, req.user_id),
        )
        _record_security_event(
            db,
            req.user_id,
            "number_activated" if req.anon_id is not None else "number_deactivated",
            (
                f"Activated {format_anon_number(owned['suffix'])} for security"
                if req.anon_id is not None
                else "Deactivated Anonymous Number security"
            ),
        )
        db.commit()
    return {"ok": True, "pinned_anon_id": req.anon_id}


# ── Blackjack ─────────────────────────────────────────────────────────────────

_bj_games: dict[int, dict] = {}

_BJ_SUITS = ['♠', '♥', '♦', '♣']
_BJ_RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']


def _bj_new_deck() -> list:
    deck = [(r, s) for s in _BJ_SUITS for r in _BJ_RANKS]
    random.shuffle(deck)
    return deck


def _bj_card_val(rank: str) -> int:
    if rank in ('J', 'Q', 'K'):
        return 10
    if rank == 'A':
        return 11
    return int(rank)


def _bj_hand_val(hand: list) -> int:
    total = sum(_bj_card_val(r) for r, _ in hand)
    aces = sum(1 for r, _ in hand if r == 'A')
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _bj_fmt(hand: list) -> list:
    return [{"rank": r, "suit": s} for r, s in hand]


def _bj_playing_state(game: dict, balance: int) -> dict:
    ci = game["current_hand"]
    hand = game["hands"][ci]
    is_first = len(hand) == 2
    can_double = is_first and balance >= game["bet"]
    can_split = (
        is_first
        and len(hand) == 2
        and _bj_card_val(hand[0][0]) == _bj_card_val(hand[1][0])
        and balance >= game["bet"]
    )
    return {
        "status": "playing",
        "bet": game["bet"],
        "hands": [_bj_fmt(h) for h in game["hands"]],
        "dealer_face": _bj_fmt([game["dealer"][0]]),
        "player_values": [_bj_hand_val(h) for h in game["hands"]],
        "dealer_value_shown": _bj_hand_val([game["dealer"][0]]),
        "current_hand": ci,
        "can_double": can_double,
        "can_split": can_split,
        "doubled": game["doubled"],
        "balance": balance,
    }


def _bj_resolve_game(db, user_id: int, game: dict) -> dict:
    dealer_hand = game["dealer"]
    deck = game["deck"]
    if any(_bj_hand_val(h) <= 21 for h in game["hands"]):
        while _bj_hand_val(dealer_hand) < 17:
            dealer_hand.append(deck.pop())
    dealer_val = _bj_hand_val(dealer_hand)

    # Bet is already deducted at game start, so:
    #   payout = what gets added back to balance (0 for loss, hand_bet for push, 2*hand_bet for win)
    #   delta  = profit/loss shown to player (-hand_bet loss, 0 push, +hand_bet win)
    total_payout = 0
    total_delta = 0
    results = []
    for i, hand in enumerate(game["hands"]):
        hand_bet = game["bet"] * (2 if game["doubled"][i] else 1)
        pv = _bj_hand_val(hand)
        if pv > 21:
            outcome, delta, payout = "bust", -hand_bet, 0
        elif dealer_val > 21 or pv > dealer_val:
            outcome, delta, payout = "win", hand_bet, 2 * hand_bet
        elif pv == dealer_val:
            outcome, delta, payout = "push", 0, hand_bet
        else:
            outcome, delta, payout = "lose", -hand_bet, 0
        results.append({"outcome": outcome, "delta": delta, "player_value": pv, "hand_bet": hand_bet})
        total_delta += delta
        total_payout += payout

    if total_payout > 0:
        db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (total_payout, user_id))
    row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)).fetchone()
    if total_delta > 0:
        _record_stats(db, user_id, blackjack_won=total_delta)
    elif total_delta < 0:
        _record_stats(db, user_id, blackjack_lost=-total_delta)
    else:
        db.commit()
    new_balance = row["balance"] if row else 0
    del _bj_games[user_id]

    return {
        "status": "finished",
        "bet": game["bet"],
        "hands": [_bj_fmt(h) for h in game["hands"]],
        "dealer_hand": _bj_fmt(dealer_hand),
        "player_values": [_bj_hand_val(h) for h in game["hands"]],
        "dealer_value": dealer_val,
        "doubled": game["doubled"],
        "results": results,
        "total_delta": total_delta,
        "new_balance": new_balance,
    }


class BlackjackStartRequest(BaseModel):
    user_id: int
    bet: int
    pp_bet: int = 0


class BlackjackActionRequest(BaseModel):
    user_id: int
    action: str


class TradeCreateRequest(BaseModel):
    from_user_id: int
    to_user_id: int
    offer_gift_id: int | None = None
    offer_anon_id: int | None = None
    offer_wrk: int = 0
    request_gift_id: int | None = None
    request_anon_id: int | None = None
    request_wrk: int = 0


class TradeActionRequest(BaseModel):
    user_id: int


@app.get("/api/blackjack/status/{user_id}")
def blackjack_status(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    game = _bj_games.get(user_id)
    if not game:
        return {"active": False}
    with db_conn() as db:
        row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        balance = row["balance"] if row else 0
    return {"active": True, **_bj_playing_state(game, balance)}


@app.post("/api/blackjack/start")
def blackjack_start(req: BlackjackStartRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    if req.user_id in _bj_games:
        raise HTTPException(400, "Game already in progress — finish it first")
    with db_conn() as db:
        row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found — use the bot first")
        if req.bet < 10:
            raise HTTPException(400, "Minimum bet is 10 WRK$")
        total_cost = req.bet + max(0, req.pp_bet)
        if row["balance"] < total_cost:
            raise HTTPException(400, f"Insufficient balance ({row['balance']:,} WRK$)")
        balance = row["balance"] - total_cost
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))

        deck = _bj_new_deck()
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]

        # Perfect Pair side bet — resolves immediately after initial deal
        pp_result, pp_delta = None, 0
        if req.pp_bet and req.pp_bet > 0:
            c1, c2 = player[0], player[1]
            if c1[0] == c2[0]:
                red = {"♥", "♦"}
                if c1[1] == c2[1]:
                    pp_result, pp_mult = "perfect", 6
                elif (c1[1] in red) == (c2[1] in red):
                    pp_result, pp_mult = "colored", 4
                else:
                    pp_result, pp_mult = "mixed", 3
                pp_delta = req.pp_bet * (pp_mult - 1)
                pp_payout = req.pp_bet * pp_mult
            else:
                pp_result, pp_delta = "none", -req.pp_bet
                pp_payout = 0
            # The side-bet stake was included in total_cost. Credit the full
            # payout on a win; a loss needs no second deduction.
            balance += pp_payout
            db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))

        player_blackjack = _bj_hand_val(player) == 21
        dealer_blackjack = _bj_hand_val(dealer) == 21
        if player_blackjack or dealer_blackjack:
            if player_blackjack and dealer_blackjack:
                outcome, main_delta, main_payout = "push", 0, req.bet
            elif player_blackjack:
                outcome = "blackjack"
                main_delta = int(req.bet * 1.5)
                main_payout = req.bet + main_delta
            else:
                outcome, main_delta, main_payout = "lose", -req.bet, 0
            balance += main_payout
            db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))
            if main_delta > 0:
                _record_stats(db, req.user_id, blackjack_won=main_delta)
            elif main_delta < 0:
                _record_stats(db, req.user_id, blackjack_lost=-main_delta)
            else:
                db.commit()
            resp = {
                "status": "blackjack" if outcome == "blackjack" else "finished",
                "bet": req.bet,
                "hands": [_bj_fmt(player)],
                "dealer_hand": _bj_fmt(dealer),
                "player_values": [_bj_hand_val(player)],
                "dealer_value": _bj_hand_val(dealer),
                "doubled": [False],
                "results": [{
                    "outcome": outcome,
                    "delta": main_delta,
                    "player_value": _bj_hand_val(player),
                    "hand_bet": req.bet,
                }],
                "total_delta": main_delta,
                "new_balance": balance,
            }
            if pp_result is not None:
                resp["pp_result"] = pp_result
                resp["pp_delta"] = pp_delta
            return resp

        db.commit()

    _bj_games[req.user_id] = {
        "bet": req.bet,
        "deck": deck,
        "hands": [player],
        "current_hand": 0,
        "doubled": [False],
        "dealer": dealer,
    }
    resp = _bj_playing_state(_bj_games[req.user_id], balance)
    if pp_result is not None:
        resp["pp_result"] = pp_result
        resp["pp_delta"] = pp_delta
    return resp


@app.post("/api/blackjack/action")
def blackjack_action(req: BlackjackActionRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    game = _bj_games.get(req.user_id)
    if not game:
        raise HTTPException(404, "No active game")
    if req.action not in ("hit", "stand", "double", "split"):
        raise HTTPException(400, "action must be hit | stand | double | split")

    with db_conn() as db:
        row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
        balance = row["balance"] if row else 0
        ci = game["current_hand"]
        hand = game["hands"][ci]

        if req.action == "hit":
            hand.append(game["deck"].pop())
            if _bj_hand_val(hand) > 21:
                if ci < len(game["hands"]) - 1:
                    game["current_hand"] += 1
                    return _bj_playing_state(game, balance)
                return _bj_resolve_game(db, req.user_id, game)
            return _bj_playing_state(game, balance)

        if req.action == "stand":
            if ci < len(game["hands"]) - 1:
                game["current_hand"] += 1
                return _bj_playing_state(game, balance)
            return _bj_resolve_game(db, req.user_id, game)

        if req.action == "double":
            if len(hand) != 2 or balance < game["bet"]:
                raise HTTPException(400, "Can't double now")
            db.execute(
                "UPDATE economy SET balance = balance - ? WHERE user_id = ?",
                (game["bet"], req.user_id),
            )
            balance -= game["bet"]
            game["doubled"][ci] = True
            hand.append(game["deck"].pop())
            if ci < len(game["hands"]) - 1:
                game["current_hand"] += 1
                db.commit()
                return _bj_playing_state(game, balance)
            return _bj_resolve_game(db, req.user_id, game)

        if req.action == "split":
            if (len(hand) != 2
                    or _bj_card_val(hand[0][0]) != _bj_card_val(hand[1][0])
                    or balance < game["bet"]):
                raise HTTPException(400, "Can't split now")
            c1, c2 = hand
            ci = game["current_hand"]
            # Insert two new hands at current position, replacing current hand
            new_hands = game["hands"][:ci] + [[c1, game["deck"].pop()], [c2, game["deck"].pop()]] + game["hands"][ci+1:]
            new_doubled = game["doubled"][:ci] + [False, False] + game["doubled"][ci+1:]
            game["hands"] = new_hands
            game["doubled"] = new_doubled
            game["current_hand"] = ci
            # Deduct extra bet for split
            db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (game["bet"], req.user_id))
            balance -= game["bet"]
            db.commit()
            return _bj_playing_state(game, balance)

    return _bj_playing_state(game, balance)  # unreachable but satisfies linter


# ── Trades ────────────────────────────────────────────────────────────────────

@app.get("/api/trades")
def get_trades(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        def _offer_row(row) -> dict:
            d = dict(row)
            d["from_display"] = _public_identity(
                db,
                d.get("from_user_id"),
                f'User {d.get("from_user_id", "?")}',
            )
            # instance_id is the offered gift column
            offer_gift_col = d.get("instance_id")
            if offer_gift_col:
                og = db.execute(
                    "SELECT gi.id, gi.gift_number, gi.background, gm.model_name, gm.model_emoji, gm.custom_emoji_id, gm.collection "
                    "FROM gift_instances gi JOIN gift_models gm ON gm.id=gi.model_id WHERE gi.id=?",
                    (offer_gift_col,)
                ).fetchone()
                d["offer_gift"] = dict(og) if og else None
            else:
                d["offer_gift"] = None
            if d.get("request_gift_id"):
                rg = db.execute(
                    "SELECT gi.id, gi.gift_number, gi.background, gm.model_name, gm.model_emoji, gm.custom_emoji_id, gm.collection "
                    "FROM gift_instances gi JOIN gift_models gm ON gm.id=gi.model_id WHERE gi.id=?",
                    (d["request_gift_id"],)
                ).fetchone()
                d["request_gift"] = dict(rg) if rg else None
            else:
                d["request_gift"] = None
            if d.get("offer_anon_id"):
                anon = db.execute(
                    "SELECT id, suffix, price FROM anon_numbers WHERE id=?",
                    (d["offer_anon_id"],),
                ).fetchone()
                d["offer_anon"] = _anon_item(anon) if anon else None
            else:
                d["offer_anon"] = None
            if d.get("request_anon_id"):
                anon = db.execute(
                    "SELECT id, suffix, price FROM anon_numbers WHERE id=?",
                    (d["request_anon_id"],),
                ).fetchone()
                d["request_anon"] = _anon_item(anon) if anon else None
            else:
                d["request_anon"] = None
            return d

        incoming = [_offer_row(r) for r in db.execute(
            "SELECT * FROM gift_offers WHERE to_user_id=? AND status='pending'", (user_id,)
        ).fetchall()]
        outgoing = [_offer_row(r) for r in db.execute(
            "SELECT * FROM gift_offers WHERE from_user_id=? AND status='pending'", (user_id,)
        ).fetchall()]
        return {"incoming": incoming, "outgoing": outgoing}


@app.post("/api/trades")
def create_trade(req: TradeCreateRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.from_user_id)
    import time as _time
    if req.from_user_id == req.to_user_id:
        raise HTTPException(400, "Cannot trade with yourself")
    if req.offer_wrk < 0 or req.request_wrk < 0:
        raise HTTPException(400, "WRK$ amounts cannot be negative")
    if req.offer_gift_id and req.offer_anon_id:
        raise HTTPException(400, "Choose one collectible for your offer")
    if req.request_gift_id and req.request_anon_id:
        raise HTTPException(400, "Choose one collectible to request")
    if (
        not req.offer_gift_id
        and not req.offer_anon_id
        and req.offer_wrk <= 0
        and not req.request_gift_id
        and not req.request_anon_id
        and req.request_wrk <= 0
    ):
        raise HTTPException(400, "Trade must have at least one item on either side")
    with db_conn() as db:
        target = db.execute(
            "SELECT 1 FROM economy WHERE user_id=?", (req.to_user_id,)
        ).fetchone()
        if not target:
            raise HTTPException(404, "Trade recipient not found")
        if req.offer_gift_id:
            row = db.execute(
                "SELECT owner_id, COALESCE(is_admin_gift, 0) AS is_admin_gift "
                "FROM gift_instances WHERE id=?",
                (req.offer_gift_id,),
            ).fetchone()
            if not row or row["owner_id"] != req.from_user_id:
                raise HTTPException(400, "You don't own that gift")
            if row["is_admin_gift"]:
                raise HTTPException(400, "Admin gifts cannot be traded")
        if req.offer_anon_id:
            row = db.execute(
                "SELECT owner_id FROM anon_numbers WHERE id=?",
                (req.offer_anon_id,),
            ).fetchone()
            if not row or row["owner_id"] != req.from_user_id:
                raise HTTPException(400, "You don't own that anonymous number")
            _assert_anon_transferable(
                db, req.from_user_id, req.offer_anon_id
            )
        if req.request_gift_id:
            row = db.execute(
                "SELECT owner_id, COALESCE(is_admin_gift, 0) AS is_admin_gift "
                "FROM gift_instances WHERE id=?",
                (req.request_gift_id,),
            ).fetchone()
            if not row or row["owner_id"] != req.to_user_id:
                raise HTTPException(400, "Target doesn't own that gift")
            if row["is_admin_gift"]:
                raise HTTPException(400, "Admin gifts cannot be traded")
        if req.request_anon_id:
            row = db.execute(
                "SELECT owner_id FROM anon_numbers WHERE id=?",
                (req.request_anon_id,),
            ).fetchone()
            if not row or row["owner_id"] != req.to_user_id:
                raise HTTPException(400, "Target doesn't own that anonymous number")
            _assert_anon_transferable(
                db, req.to_user_id, req.request_anon_id
            )
        if req.offer_wrk > 0:
            bal = db.execute("SELECT balance FROM economy WHERE user_id=?", (req.from_user_id,)).fetchone()
            if not bal or bal["balance"] < req.offer_wrk:
                raise HTTPException(400, "Insufficient balance for WRK$ offer")
        db.execute(
            "INSERT INTO gift_offers "
            "(from_user_id, to_user_id, instance_id, wrk_offered, "
            "request_gift_id, request_wrk, offer_anon_id, request_anon_id, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (req.from_user_id, req.to_user_id,
             req.offer_gift_id, req.offer_wrk,
             req.request_gift_id, req.request_wrk,
             req.offer_anon_id, req.request_anon_id,
             "pending", int(_time.time()))
        )
        db.commit()
        return {"ok": True}


@app.post("/api/trades/{offer_id}/accept")
def accept_trade(offer_id: int, req: TradeActionRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute("BEGIN IMMEDIATE")
        offer = db.execute("SELECT * FROM gift_offers WHERE id=?", (offer_id,)).fetchone()
        if not offer:
            raise HTTPException(404, "Offer not found")
        if offer["to_user_id"] != req.user_id:
            raise HTTPException(403, "Not your offer to accept")
        if offer["status"] != "pending":
            raise HTTPException(400, "Offer is no longer pending")

        from_id = offer["from_user_id"]
        to_id = offer["to_user_id"]
        offered_gift = offer["instance_id"]
        requested_gift = offer["request_gift_id"]
        offered_anon = offer["offer_anon_id"]
        requested_anon = offer["request_anon_id"]

        if offered_gift:
            row = db.execute(
                "SELECT owner_id, COALESCE(is_admin_gift, 0) AS is_admin_gift "
                "FROM gift_instances WHERE id=?",
                (offered_gift,),
            ).fetchone()
            if not row or row["owner_id"] != from_id:
                raise HTTPException(400, "Sender no longer owns the offered gift")
            if row["is_admin_gift"]:
                raise HTTPException(400, "Admin gifts cannot be traded")
        if requested_gift:
            row = db.execute(
                "SELECT owner_id, COALESCE(is_admin_gift, 0) AS is_admin_gift "
                "FROM gift_instances WHERE id=?",
                (requested_gift,),
            ).fetchone()
            if not row or row["owner_id"] != to_id:
                raise HTTPException(400, "You no longer own the requested gift")
            if row["is_admin_gift"]:
                raise HTTPException(400, "Admin gifts cannot be traded")
        if offered_anon:
            row = db.execute(
                "SELECT owner_id FROM anon_numbers WHERE id=?", (offered_anon,)
            ).fetchone()
            if not row or row["owner_id"] != from_id:
                raise HTTPException(
                    400, "Sender no longer owns the offered anonymous number"
                )
            _assert_anon_transferable(db, from_id, offered_anon)
        if requested_anon:
            row = db.execute(
                "SELECT owner_id FROM anon_numbers WHERE id=?", (requested_anon,)
            ).fetchone()
            if not row or row["owner_id"] != to_id:
                raise HTTPException(
                    400, "You no longer own the requested anonymous number"
                )
            _assert_anon_transferable(db, to_id, requested_anon)
        wrk_offered = offer["wrk_offered"]
        if wrk_offered > 0:
            bal = db.execute("SELECT balance FROM economy WHERE user_id=?", (from_id,)).fetchone()
            if not bal or bal["balance"] < wrk_offered:
                raise HTTPException(400, "Sender has insufficient balance")
        if offer["request_wrk"] > 0:
            bal = db.execute("SELECT balance FROM economy WHERE user_id=?", (to_id,)).fetchone()
            if not bal or bal["balance"] < offer["request_wrk"]:
                raise HTTPException(400, "Insufficient balance to meet WRK$ request")

        now = int(time.time())
        if offered_gift:
            db.execute(
                "UPDATE gift_instances SET owner_id=?, acquired_at=? WHERE id=?",
                (to_id, now, offered_gift),
            )
            db.execute(
                "UPDATE economy SET pinned_gift_id=NULL "
                "WHERE user_id=? AND pinned_gift_id=?",
                (from_id, offered_gift),
            )
        if requested_gift:
            db.execute(
                "UPDATE gift_instances SET owner_id=?, acquired_at=? WHERE id=?",
                (from_id, now, requested_gift),
            )
            db.execute(
                "UPDATE economy SET pinned_gift_id=NULL "
                "WHERE user_id=? AND pinned_gift_id=?",
                (to_id, requested_gift),
            )
        for gift_id in (offered_gift, requested_gift):
            if gift_id:
                db.execute(
                    "UPDATE gift_market_listings SET status='cancelled' "
                    "WHERE gift_id=? AND status='active'",
                    (gift_id,),
                )
        if offered_anon:
            db.execute(
                "UPDATE anon_numbers SET owner_id=?, acquired_at=? WHERE id=?",
                (to_id, now, offered_anon),
            )
            db.execute(
                "UPDATE economy SET pinned_anon_id=NULL, anon_mask_enabled=0 "
                "WHERE user_id=? AND pinned_anon_id=?",
                (from_id, offered_anon),
            )
        if requested_anon:
            db.execute(
                "UPDATE anon_numbers SET owner_id=?, acquired_at=? WHERE id=?",
                (from_id, now, requested_anon),
            )
            db.execute(
                "UPDATE economy SET pinned_anon_id=NULL, anon_mask_enabled=0 "
                "WHERE user_id=? AND pinned_anon_id=?",
                (to_id, requested_anon),
            )
        if wrk_offered > 0:
            db.execute("UPDATE economy SET balance=balance-? WHERE user_id=?", (wrk_offered, from_id))
            db.execute("UPDATE economy SET balance=balance+? WHERE user_id=?", (wrk_offered, to_id))
        if offer["request_wrk"] > 0:
            db.execute("UPDATE economy SET balance=balance-? WHERE user_id=?", (offer["request_wrk"], to_id))
            db.execute("UPDATE economy SET balance=balance+? WHERE user_id=?", (offer["request_wrk"], from_id))

        db.execute("UPDATE gift_offers SET status='accepted' WHERE id=?", (offer_id,))
        for gid in (offered_gift, requested_gift):
            if gid:
                db.execute(
                    "UPDATE gift_offers SET status='rejected' WHERE id!=? AND status='pending' "
                    "AND (instance_id=? OR request_gift_id=?)",
                    (offer_id, gid, gid)
                )
        for anon_id in (offered_anon, requested_anon):
            if anon_id:
                db.execute(
                    "UPDATE gift_offers SET status='rejected' "
                    "WHERE id!=? AND status='pending' "
                    "AND (offer_anon_id=? OR request_anon_id=?)",
                    (offer_id, anon_id, anon_id),
                )
        db.commit()
        return {"ok": True}


@app.post("/api/trades/{offer_id}/reject")
def reject_trade(offer_id: int, req: TradeActionRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        offer = db.execute("SELECT * FROM gift_offers WHERE id=?", (offer_id,)).fetchone()
        if not offer or offer["to_user_id"] != req.user_id:
            raise HTTPException(403, "Not your offer to reject")
        if offer["status"] != "pending":
            raise HTTPException(400, "Offer is no longer pending")
        db.execute("UPDATE gift_offers SET status='rejected' WHERE id=?", (offer_id,))
        db.commit()
        return {"ok": True}


@app.post("/api/trades/{offer_id}/cancel")
def cancel_trade(offer_id: int, req: TradeActionRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        offer = db.execute("SELECT * FROM gift_offers WHERE id=?", (offer_id,)).fetchone()
        if not offer or offer["from_user_id"] != req.user_id:
            raise HTTPException(403, "Not your offer to cancel")
        if offer["status"] != "pending":
            raise HTTPException(400, "Offer is no longer pending")
        db.execute("UPDATE gift_offers SET status='cancelled' WHERE id=?", (offer_id,))
        db.commit()
        return {"ok": True}


# ── Lobby WebSocket ───────────────────────────────────────────────────────────

_lobby_connections: set[WebSocket] = set()
_lobby_event_seq = 0
_lobby_recent_events: list[dict] = []


async def _lobby_broadcast(msg: dict) -> None:
    global _lobby_event_seq
    _lobby_event_seq += 1
    event = {
        **msg,
        "event_id": _lobby_event_seq,
        "created_at": int(time.time()),
    }
    _lobby_recent_events.append(event)
    del _lobby_recent_events[:-30]
    dead = set()
    for ws in list(_lobby_connections):
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    _lobby_connections.difference_update(dead)


@app.get("/api/lobby/events")
def lobby_events(authenticated_user: AuthenticatedUser, after: int = 0):
    cutoff = int(time.time()) - 45
    events = [
        event for event in _lobby_recent_events
        if event["event_id"] > max(0, after)
        and event["created_at"] >= cutoff
        and event.get("user_id") != authenticated_user
    ]
    return {"events": events, "latest_event_id": _lobby_event_seq}


# ── Crash ─────────────────────────────────────────────────────────────────────

_CRASH_BETTING_SECS = 30.0
_CRASH_TICK_MS = 100
_CRASH_GROWTH = 0.015  # 1.5% per tick → ~2× at 5s, ~4.4× at 10s


class _CrashState:
    def __init__(self):
        self.phase = "waiting"
        self.multiplier = 1.0
        self.crash_point = 1.0
        self.countdown = _CRASH_BETTING_SECS
        self.history: list[float] = []
        self.bets: dict[int, dict] = {}  # user_id -> {bet, cashed_out}
        self.names: dict[int, str] = {}      # user_id -> display name
        self.connections: set[WebSocket] = set()


_crash = _CrashState()


def _gen_crash_point() -> float:
    r = random.random()
    if r < 0.03:
        return 1.0
    cp = 0.97 / (1 - r)
    return round(min(cp, 1000.0), 2)


async def _crash_broadcast(msg: dict):
    dead = set()
    for ws in list(_crash.connections):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _crash.connections -= dead


def _crash_snapshot() -> dict:
    players = [
        {
            "name": _crash.names.get(uid, str(uid)),
            "bet": info["bet"],
            "cashed_out": info["cashed_out"],
            "mult": info.get("mult"),   # None if still in, float if cashed out or crashed
        }
        for uid, info in _crash.bets.items()
    ]
    return {
        "phase": _crash.phase,
        "multiplier": _crash.multiplier,
        "countdown": round(_crash.countdown, 1),
        "history": _crash.history[-10:],
        "players": players,
    }


async def _crash_loop():
    while True:
        try:
            # Betting phase
            _crash.phase = "waiting"
            _crash.bets = {}
            _crash.names = {}
            _crash.multiplier = 1.0
            _crash.crash_point = _gen_crash_point()
            deadline = asyncio.get_running_loop().time() + _CRASH_BETTING_SECS

            while True:
                _crash.countdown = max(0.0, deadline - asyncio.get_running_loop().time())
                await _crash_broadcast({"type": "state", **_crash_snapshot()})
                if _crash.countdown <= 0:
                    break
                await asyncio.sleep(0.5)

            # Running phase
            _crash.phase = "running"
            _crash.multiplier = 1.0
            _crash.countdown = 0.0

            while _crash.multiplier < _crash.crash_point:
                await asyncio.sleep(_CRASH_TICK_MS / 1000)
                _crash.multiplier = round(_crash.multiplier * (1 + _CRASH_GROWTH), 2)
                if _crash.multiplier >= _crash.crash_point:
                    _crash.multiplier = _crash.crash_point
                await _crash_broadcast({"type": "state", **_crash_snapshot()})

            # Crash — record losses for players who didn't cash out
            _crash.phase = "crashed"
            _crash.history.append(_crash.crash_point)
            _crash.history = _crash.history[-10:]
            losers = [(uid, info["bet"]) for uid, info in _crash.bets.items() if not info["cashed_out"]]
            if losers:
                with db_conn() as db:
                    for uid, lost in losers:
                        _record_stats(db, uid, crash_lost=lost)
            await _crash_broadcast({"type": "crashed", **_crash_snapshot()})
            await asyncio.sleep(3.0)

        except Exception:
            await asyncio.sleep(2.0)


# ── Presence ─────────────────────────────────────────────────────────────────

class PresencePingRequest(BaseModel):
    user_id: int

@app.post("/api/presence/ping")
def presence_ping(req: PresencePingRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        db.execute(
            "INSERT INTO online_sessions (user_id, last_ping) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_ping=excluded.last_ping",
            (req.user_id, int(time.time()))
        )
        db.commit()
    return {"ok": True}

@app.get("/api/presence/online")
def presence_online():
    threshold = int(time.time()) - 60
    with db_conn() as db:
        rows = db.execute(
            "SELECT user_id FROM online_sessions WHERE last_ping > ?", (threshold,)
        ).fetchall()
    return {"online": [r["user_id"] for r in rows]}


# ── Friendships ───────────────────────────────────────────────────────────────

class FriendRequestCreate(BaseModel):
    from_user_id: int
    to_user_id: int

class FriendActionRequest(BaseModel):
    user_id: int

@app.get("/api/friends")
def get_friends(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        def _enrich(row):
            d = dict(row)
            other_id = d["to_user_id"] if d["from_user_id"] == user_id else d["from_user_id"]
            d["other_user_id"] = other_id
            d["other_display"] = _public_identity(
                db, other_id, f"User {other_id}"
            )
            return d

        friends = [_enrich(r) for r in db.execute(
            "SELECT * FROM friendships WHERE status='accepted' AND (from_user_id=? OR to_user_id=?)",
            (user_id, user_id)
        ).fetchall()]
        incoming = [_enrich(r) for r in db.execute(
            "SELECT * FROM friendships WHERE to_user_id=? AND status='pending'", (user_id,)
        ).fetchall()]
        outgoing = [_enrich(r) for r in db.execute(
            "SELECT * FROM friendships WHERE from_user_id=? AND status='pending'", (user_id,)
        ).fetchall()]
        return {"friends": friends, "incoming": incoming, "outgoing": outgoing}

class DailyClaimRequest(BaseModel):
    user_id: int

@app.post("/api/daily")
def claim_daily(req: DailyClaimRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    now = int(time.time())
    COOLDOWN = 86400
    with db_conn() as db:
        row = db.execute(
            "SELECT balance, streak, last_daily FROM economy WHERE user_id = ?",
            (req.user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        last = row["last_daily"] or 0
        if now - last < COOLDOWN:
            remaining = COOLDOWN - (now - last)
            h, m = divmod(remaining // 60, 60)
            raise HTTPException(400, f"Already claimed. Next in {h}h {m}m.")
        streak = row["streak"] or 0
        if last > 0 and now - last > 172800:
            streak = 0
        streak += 1
        mult = 4 if streak >= 30 else 3 if streak >= 14 else 2 if streak >= 7 else 1
        earned = random.randint(3000, 8000) * mult
        db.execute(
            "UPDATE economy SET balance = balance + ?, streak = ?, last_daily = ? WHERE user_id = ?",
            (earned, streak, now, req.user_id)
        )
        new_balance = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()["balance"]
        db.commit()
    return {
        "earned": earned,
        "streak": streak,
        "mult": mult,
        "new_balance": new_balance,
    }

@app.get("/api/daily/status")
def daily_status(user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    now = int(time.time())
    COOLDOWN = 86400
    with db_conn() as db:
        row = db.execute(
            "SELECT streak, last_daily FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        last = row["last_daily"] or 0
        remaining = max(0, COOLDOWN - (now - last))
        h, m = divmod(remaining // 60, 60)
        return {
            "can_claim": remaining == 0,
            "remaining_secs": remaining,
            "remaining_label": f"{h}h {m}m" if remaining > 0 else "Ready!",
            "streak": row["streak"] or 0,
        }


class SendWrkRequest(BaseModel):
    from_user_id: int
    to_user_id: int
    amount: int
    from_display: str = ""
    to_display: str = ""

@app.post("/api/send-wrk")
def send_wrk(req: SendWrkRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.from_user_id)
    if req.from_user_id == req.to_user_id:
        raise HTTPException(400, "Cannot send to yourself")
    if req.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    with db_conn() as db:
        sender = db.execute("SELECT balance, username, full_name FROM economy WHERE user_id=?", (req.from_user_id,)).fetchone()
        if not sender or sender["balance"] < req.amount:
            raise HTTPException(400, "Insufficient balance")
        target = db.execute("SELECT user_id, username, full_name FROM economy WHERE user_id=?", (req.to_user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "Recipient not found")
        db.execute("UPDATE economy SET balance=balance-? WHERE user_id=?", (req.amount, req.from_user_id))
        db.execute("UPDATE economy SET balance=balance+? WHERE user_id=?", (req.amount, req.to_user_id))
        new_bal = db.execute("SELECT balance FROM economy WHERE user_id=?", (req.from_user_id,)).fetchone()["balance"]
        from_name = _public_identity(db, req.from_user_id)
        to_name = _public_identity(db, req.to_user_id)
        db.commit()

    # Send DM confirmations
    amt_fmt = f"{req.amount:,}"
    _send_telegram_dm(
        req.from_user_id,
        f"✅ You sent {amt_fmt} WRK$ to {to_name}.\nBalance left: {new_bal:,} WRK$"
    )
    _send_telegram_dm(
        req.to_user_id,
        f"💸 {from_name} sent you {amt_fmt} WRK$!"
    )

    return {"ok": True, "new_balance": new_bal}


@app.post("/api/admin/poker-reset")
def admin_poker_reset(authenticated_user: AuthenticatedUser):
    _require_owner(authenticated_user)
    with db_conn() as db:
        for seat in _poker.seats:
            db.execute(
                "UPDATE economy SET balance = balance + ? WHERE user_id = ?",
                (seat.get("chips", 0), seat["user_id"])
            )
        db.commit()
    _poker.seats.clear()
    _poker.connections.clear()
    _poker.phase = "lobby"
    _poker.pot = 0
    _poker.community = []
    return {"ok": True, "message": "Poker table reset, chips refunded"}


class AdminGiftGrantRequest(BaseModel):
    user_id: int
    collection: str = "Admin's Plush Pepe"

@app.post("/api/admin/grant-admin-gift")
def grant_admin_gift(req: AdminGiftGrantRequest, authenticated_user: AuthenticatedUser):
    _require_owner(authenticated_user)
    with db_conn() as db:
        model = db.execute(
            "SELECT id FROM gift_models WHERE collection = ? LIMIT 1",
            (req.collection,)
        ).fetchone()
        if not model:
            raise HTTPException(404, f"No model found for collection '{req.collection}'")
        # Sequential gift number: count all previously granted + 1
        count_row = db.execute(
            "SELECT COUNT(*) FROM gift_instances WHERE model_id = ? AND is_admin_gift = 1",
            (model["id"],)
        ).fetchone()
        next_number = (count_row[0] or 0) + 1
        db.execute(
            "INSERT INTO gift_instances (model_id, owner_id, background, gift_number, is_admin_gift, staked) "
            "VALUES (?,?,?,?,1,0)",
            (model["id"], req.user_id, "black", next_number)
        )
        db.commit()
    return {"ok": True, "message": f"Admin's Plush Pepe #{next_number} granted to user {req.user_id}"}


@app.post("/api/friends/request")
def send_friend_request(req: FriendRequestCreate, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.from_user_id)
    import time as _time
    if req.from_user_id == req.to_user_id:
        raise HTTPException(400, "Cannot add yourself")
    with db_conn() as db:
        existing = db.execute(
            "SELECT id, status FROM friendships WHERE "
            "(from_user_id=? AND to_user_id=?) OR (from_user_id=? AND to_user_id=?)",
            (req.from_user_id, req.to_user_id, req.to_user_id, req.from_user_id)
        ).fetchone()
        if existing:
            if existing["status"] == "accepted":
                raise HTTPException(400, "Already friends")
            if existing["status"] == "pending":
                raise HTTPException(400, "Request already pending")
        db.execute(
            "INSERT INTO friendships (from_user_id, to_user_id, status, created_at) VALUES (?,?,?,?)",
            (req.from_user_id, req.to_user_id, "pending", int(_time.time()))
        )
        db.commit()
    return {"ok": True}

@app.post("/api/friends/{friendship_id}/accept")
def accept_friend(friendship_id: int, req: FriendActionRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        row = db.execute("SELECT * FROM friendships WHERE id=?", (friendship_id,)).fetchone()
        if not row or row["to_user_id"] != req.user_id:
            raise HTTPException(403, "Not your request to accept")
        if row["status"] != "pending":
            raise HTTPException(400, "Not pending")
        db.execute("UPDATE friendships SET status='accepted' WHERE id=?", (friendship_id,))
        db.commit()
    return {"ok": True}

@app.post("/api/friends/{friendship_id}/decline")
def decline_friend(friendship_id: int, req: FriendActionRequest, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, req.user_id)
    with db_conn() as db:
        row = db.execute("SELECT * FROM friendships WHERE id=?", (friendship_id,)).fetchone()
        if not row or row["to_user_id"] != req.user_id:
            raise HTTPException(403, "Not your request to decline")
        db.execute("UPDATE friendships SET status='declined' WHERE id=?", (friendship_id,))
        db.commit()
    return {"ok": True}

@app.delete("/api/friends/{friendship_id}")
def remove_friend(friendship_id: int, user_id: int, authenticated_user: AuthenticatedUser):
    _require_actor(authenticated_user, user_id)
    with db_conn() as db:
        row = db.execute("SELECT * FROM friendships WHERE id=?", (friendship_id,)).fetchone()
        if not row or (row["from_user_id"] != user_id and row["to_user_id"] != user_id):
            raise HTTPException(403, "Not your friendship")
        db.execute("DELETE FROM friendships WHERE id=?", (friendship_id,))
        db.commit()
    return {"ok": True}


@app.get("/api/game-timers")
def game_timers():
    def _crash_info():
        if _crash.phase == "waiting":
            return {"phase": "waiting", "countdown": round(_crash.countdown, 1)}
        return {"phase": _crash.phase, "countdown": 0}

    def _duck_info():
        if _duck.phase == "waiting":
            return {"phase": "waiting", "countdown": round(_duck.countdown, 1)}
        return {"phase": _duck.phase, "countdown": 0}

    def _marble_info():
        if _marble.phase == "open":
            return {"phase": "waiting", "countdown": round(_marble.countdown, 1)}
        return {"phase": _marble.phase, "countdown": 0}

    def _livebj_info():
        if _livebj.phase == "waiting":
            return {"phase": "waiting", "countdown": round(_livebj.countdown, 1)}
        return {"phase": _livebj.phase, "countdown": 0}

    return {
        "crash": _crash_info(),
        "duck": _duck_info(),
        "marbles": _marble_info(),
        "livebj": _livebj_info(),
    }


async def _startup():
    with db_conn() as db:
        for col in (
            "pinned_gift_id INTEGER",
            "pinned_anon_id INTEGER",
            "photo_url TEXT",
            "secure_vault_balance INTEGER NOT NULL DEFAULT 0",
            "vault_pending_amount INTEGER NOT NULL DEFAULT 0",
            "vault_withdraw_available_at INTEGER NOT NULL DEFAULT 0",
            "anon_mask_enabled INTEGER NOT NULL DEFAULT 0",
            "anon_firewall_used_at INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                db.execute(f"ALTER TABLE economy ADD COLUMN {col}")
                db.commit()
            except Exception:
                pass
        db.execute("""CREATE TABLE IF NOT EXISTS game_stats (
            user_id         INTEGER PRIMARY KEY,
            slots_won       INTEGER NOT NULL DEFAULT 0,
            slots_lost      INTEGER NOT NULL DEFAULT 0,
            coinflip_won    INTEGER NOT NULL DEFAULT 0,
            coinflip_lost   INTEGER NOT NULL DEFAULT 0,
            blackjack_won   INTEGER NOT NULL DEFAULT 0,
            blackjack_lost  INTEGER NOT NULL DEFAULT 0,
            crash_won       INTEGER NOT NULL DEFAULT 0,
            crash_lost      INTEGER NOT NULL DEFAULT 0,
            crash_best_mult REAL    NOT NULL DEFAULT 0
        )""")
        db.commit()
        for col in ("last_rob INTEGER NOT NULL DEFAULT 0", "last_hack INTEGER NOT NULL DEFAULT 0"):
            try:
                db.execute(f"ALTER TABLE economy ADD COLUMN {col}")
                db.commit()
            except Exception:
                pass
        try:
            db.execute("ALTER TABLE gift_instances ADD COLUMN sort_index INTEGER")
            db.commit()
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE gift_instances ADD COLUMN staked INTEGER DEFAULT 0")
            db.commit()
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE gift_instances ADD COLUMN is_admin_gift INTEGER DEFAULT 0")
            db.commit()
        except Exception:
            pass
        db.execute("""CREATE TABLE IF NOT EXISTS hack_sessions (
            user_id          INTEGER PRIMARY KEY,
            word             TEXT    NOT NULL,
            clue             TEXT    NOT NULL,
            reward           INTEGER NOT NULL,
            attempts         INTEGER NOT NULL DEFAULT 5,
            revealed_indices TEXT    NOT NULL DEFAULT '0',
            started_at       INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS craps_sessions (
            user_id    INTEGER PRIMARY KEY,
            bet        INTEGER NOT NULL,
            point      INTEGER,
            started_at INTEGER NOT NULL
        )""")
        try:
            db.execute("ALTER TABLE craps_sessions ADD COLUMN roll_count INTEGER NOT NULL DEFAULT 0")
            db.commit()
        except Exception:
            pass
        db.execute("""CREATE TABLE IF NOT EXISTS highlow_sessions (
            user_id      INTEGER PRIMARY KEY,
            bet          INTEGER NOT NULL,
            current_card INTEGER NOT NULL,
            multiplier   REAL    NOT NULL DEFAULT 1.0,
            started_at   INTEGER NOT NULL
        )""")
        try:
            db.execute("ALTER TABLE highlow_sessions ADD COLUMN deck TEXT")
            db.commit()
        except Exception:
            pass
        db.execute("""
    CREATE TABLE IF NOT EXISTS cases_opened (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        tier TEXT NOT NULL,
        wrk_reward INTEGER NOT NULL DEFAULT 0,
        gift_id INTEGER,
        opened_at INTEGER NOT NULL
    )
""")
        db.execute("""CREATE TABLE IF NOT EXISTS online_sessions (
    user_id   INTEGER PRIMARY KEY,
    last_ping INTEGER NOT NULL
)""")
        db.execute("""CREATE TABLE IF NOT EXISTS friendships (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id  INTEGER NOT NULL,
    to_user_id    INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    INTEGER NOT NULL,
    UNIQUE(from_user_id, to_user_id)
)""")
        db.execute("""CREATE TABLE IF NOT EXISTS gift_market_listings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    gift_id    INTEGER NOT NULL REFERENCES gift_instances(id),
    seller_id  INTEGER NOT NULL,
    price      INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    buyer_id   INTEGER,
    created_at INTEGER NOT NULL,
    sold_at    INTEGER
)""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_gift_market_active
    ON gift_market_listings(gift_id) WHERE status = 'active'""")
        db.execute("""CREATE TABLE IF NOT EXISTS anon_numbers (
    id          INTEGER PRIMARY KEY,
    suffix      INTEGER NOT NULL UNIQUE,
    price       INTEGER NOT NULL,
    owner_id    INTEGER,
    acquired_at INTEGER
)""")
        db.execute("""CREATE TABLE IF NOT EXISTS anon_security_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail     TEXT NOT NULL,
    amount     INTEGER NOT NULL DEFAULT 0,
    actor_id   INTEGER,
    created_at INTEGER NOT NULL
)""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_anon_security_events_user
    ON anon_security_events(user_id, created_at DESC)""")
        db.executemany(
            "INSERT OR IGNORE INTO anon_numbers (id, suffix, price) VALUES (?, ?, ?)",
            [
                (suffix, suffix, anon_number_price(suffix))
                for suffix in range(ANON_MIN_SUFFIX, ANON_MAX_SUFFIX + 1)
            ],
        )
        db.commit()
        for col in ("duck_won INTEGER DEFAULT 0", "duck_lost INTEGER DEFAULT 0",
                    "marbles_won INTEGER DEFAULT 0", "marbles_lost INTEGER DEFAULT 0",
                    "livebj_won INTEGER DEFAULT 0", "livebj_lost INTEGER DEFAULT 0",
                    "poker_won INTEGER DEFAULT 0", "poker_lost INTEGER DEFAULT 0"):
            try:
                db.execute(f"ALTER TABLE game_stats ADD COLUMN {col}")
                db.commit()
            except Exception:
                pass
        for col in (
            "roulette_won INTEGER DEFAULT 0", "roulette_lost INTEGER DEFAULT 0",
            "plinko_won INTEGER DEFAULT 0",   "plinko_lost INTEGER DEFAULT 0",
            "wheel_won INTEGER DEFAULT 0",    "wheel_lost INTEGER DEFAULT 0",
            "slider_won INTEGER DEFAULT 0",   "slider_lost INTEGER DEFAULT 0",
            "craps_won INTEGER DEFAULT 0",    "craps_lost INTEGER DEFAULT 0",
            "highlow_won INTEGER DEFAULT 0",  "highlow_lost INTEGER DEFAULT 0",
            "cases_won INTEGER DEFAULT 0",    "cases_lost INTEGER DEFAULT 0",
        ):
            try:
                db.execute(f"ALTER TABLE game_stats ADD COLUMN {col}")
                db.commit()
            except Exception:
                pass
        # Older installs require an offered gift. Rebuild that table once so
        # WRK$-only and anonymous-number trades can use NULL instance_id.
        trade_info = db.execute("PRAGMA table_info(gift_offers)").fetchall()
        trade_cols = {row["name"]: row for row in trade_info}
        instance_col = trade_cols.get("instance_id")
        if instance_col and instance_col["notnull"]:
            db.execute("DROP TABLE IF EXISTS gift_offers_trade_migration")
            db.execute("""CREATE TABLE gift_offers_trade_migration (
                id              INTEGER PRIMARY KEY,
                from_user_id    INTEGER NOT NULL,
                to_user_id      INTEGER NOT NULL,
                instance_id     INTEGER REFERENCES gift_instances(id),
                wrk_offered     INTEGER NOT NULL DEFAULT 0,
                request_gift_id INTEGER REFERENCES gift_instances(id),
                request_wrk     INTEGER NOT NULL DEFAULT 0,
                offer_anon_id   INTEGER REFERENCES anon_numbers(id),
                request_anon_id INTEGER REFERENCES anon_numbers(id),
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      INTEGER NOT NULL
            )""")
            copy_expr = {
                "id": "id",
                "from_user_id": "from_user_id",
                "to_user_id": "to_user_id",
                "instance_id": "instance_id",
                "wrk_offered": "wrk_offered",
                "request_gift_id": (
                    "request_gift_id"
                    if "request_gift_id" in trade_cols else "NULL"
                ),
                "request_wrk": (
                    "request_wrk" if "request_wrk" in trade_cols else "0"
                ),
                "offer_anon_id": (
                    "offer_anon_id" if "offer_anon_id" in trade_cols else "NULL"
                ),
                "request_anon_id": (
                    "request_anon_id"
                    if "request_anon_id" in trade_cols else "NULL"
                ),
                "status": "status",
                "created_at": "created_at",
            }
            names = ", ".join(copy_expr)
            values = ", ".join(copy_expr.values())
            db.execute(
                f"INSERT INTO gift_offers_trade_migration ({names}) "
                f"SELECT {values} FROM gift_offers"
            )
            db.execute("DROP TABLE gift_offers")
            db.execute(
                "ALTER TABLE gift_offers_trade_migration RENAME TO gift_offers"
            )
            db.commit()
        else:
            for col, typedef in (
                ("request_gift_id", "INTEGER REFERENCES gift_instances(id)"),
                ("request_wrk", "INTEGER NOT NULL DEFAULT 0"),
                ("offer_anon_id", "INTEGER REFERENCES anon_numbers(id)"),
                ("request_anon_id", "INTEGER REFERENCES anon_numbers(id)"),
            ):
                if col not in trade_cols:
                    db.execute(
                        f"ALTER TABLE gift_offers ADD COLUMN {col} {typedef}"
                    )
            db.commit()
        try:
            db.execute("ALTER TABLE economy ADD COLUMN pinned_stat TEXT NOT NULL DEFAULT 'crash_mult'")
            db.commit()
        except Exception:
            pass

    return [
        asyncio.create_task(_crash_loop()),
        asyncio.create_task(_duck_loop()),
        asyncio.create_task(_marble_loop()),
        asyncio.create_task(_livebj_loop()),
        asyncio.create_task(_poker_loop()),
    ]


@app.websocket("/ws/crash")
async def crash_ws(ws: WebSocket):
    authenticated_user = await _accept_authenticated_websocket(ws)
    if authenticated_user is None:
        return
    _crash.connections.add(ws)
    await ws.send_json({"type": "state", **_crash_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = authenticated_user
            if data.get("user_id") is not None and int(data["user_id"]) != uid:
                await ws.send_json({"type": "error", "message": "User identity mismatch"})
                continue

            if data.get("type") == "bet":
                amount = int(data.get("amount", 0))
                if _crash.phase != "waiting":
                    await ws.send_json({"type": "error", "message": "Betting phase has ended"})
                    continue
                if uid in _crash.bets:
                    await ws.send_json({"type": "error", "message": "Already bet this round"})
                    continue
                if amount < 10:
                    await ws.send_json({"type": "error", "message": "Minimum bet is 10 WRK$"})
                    continue
                with db_conn() as db:
                    row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()
                    if not row:
                        await ws.send_json({"type": "error", "message": "User not found — use the bot first"})
                        continue
                    if row["balance"] < amount:
                        await ws.send_json({"type": "error", "message": f"Insufficient balance ({row['balance']:,} WRK$)"})
                        continue
                    db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (amount, uid))
                    new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()["balance"]
                    _crash.names[uid] = _public_identity(
                        db, uid, f"Player {uid}"
                    )
                    db.commit()
                _crash.bets[uid] = {"bet": amount, "cashed_out": False}
                if len(_crash.names) == 1:
                    await _lobby_broadcast({
                        "type": "player_joined",
                        "game": "crash",
                        "game_label": "Crash",
                        "user": _crash.names[uid],
                        "user_id": uid,
                    })
                await ws.send_json({"type": "bet_placed", "bet": amount, "new_balance": new_bal})

            elif data.get("type") == "cashout":
                if _crash.phase != "running":
                    await ws.send_json({"type": "error", "message": "Game is not running"})
                    continue
                bet_info = _crash.bets.get(uid)
                if not bet_info or bet_info["cashed_out"]:
                    await ws.send_json({"type": "error", "message": "No active bet"})
                    continue
                mult = _crash.multiplier
                payout = int(bet_info["bet"] * mult)
                profit = payout - bet_info["bet"]
                bet_info["cashed_out"] = True
                bet_info["mult"] = mult
                with db_conn() as db:
                    db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (payout, uid))
                    new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()["balance"]
                    _record_stats(db, uid, crash_won=profit, crash_mult=mult)
                await ws.send_json({"type": "cashed_out", "multiplier": mult, "payout": payout,
                                    "profit": profit, "new_balance": new_bal})

    except WebSocketDisconnect:
        _crash.connections.discard(ws)
    except Exception:
        _crash.connections.discard(ws)


# ── Duck Racing ───────────────────────────────────────────────────────────────

_DUCK_WAITING_SECS = 15
_DUCK_RACE_SECS = 8
_DUCK_FINISHED_SECS = 5
_DUCK_NAMES = ["Quackers", "Sir Ducks-a-Lot", "Donald", "Daffy"]
_DUCK_EMOJIS = ["🦆", "🐤", "🐥", "🦅"]

# Preset multiplier pools — each sums to ~4.255 in inverse (94% RTP)
_DUCK_MULT_POOLS = [
    [1.4, 2.2, 3.8, 7.5],
    [1.5, 2.0, 3.5, 9.0],
    [1.6, 2.4, 3.2, 6.5],
    [1.3, 2.8, 4.0, 8.0],
    [1.7, 2.1, 3.6, 8.5],
]


class _DuckState:
    def __init__(self):
        self.phase = "waiting"
        self.ducks: list[dict] = []        # [{name, emoji, mult}]
        self.bets: dict[int, dict] = {}    # uid -> {duck_idx, bet, name}
        self.winner_idx: int | None = None
        self.countdown: float = _DUCK_WAITING_SECS
        self.connections: set[WebSocket] = set()


_duck = _DuckState()


async def _duck_broadcast(msg: dict):
    dead = set()
    for ws in list(_duck.connections):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _duck.connections -= dead


def _duck_snapshot() -> dict:
    return {
        "phase": _duck.phase,
        "ducks": _duck.ducks,
        "bets": [
            {"name": v["name"], "duck_idx": v["duck_idx"], "bet": v["bet"]}
            for v in _duck.bets.values()
        ],
        "winner_idx": _duck.winner_idx,
        "countdown": round(_duck.countdown, 1),
    }


async def _duck_loop():
    while True:
        try:
            # Setup
            _duck.phase = "waiting"
            _duck.bets = {}
            _duck.winner_idx = None
            pool = random.choice(_DUCK_MULT_POOLS)
            mults = pool[:]
            random.shuffle(mults)
            _duck.ducks = [
                {"name": _DUCK_NAMES[i], "emoji": _DUCK_EMOJIS[i], "mult": mults[i]}
                for i in range(4)
            ]
            deadline = asyncio.get_running_loop().time() + _DUCK_WAITING_SECS

            while True:
                _duck.countdown = max(0.0, deadline - asyncio.get_running_loop().time())
                await _duck_broadcast({"type": "state", **_duck_snapshot()})
                if _duck.countdown <= 0:
                    break
                await asyncio.sleep(0.5)

            # Pick winner by weighted probability
            inv = [1 / d["mult"] for d in _duck.ducks]
            total_inv = sum(inv)
            r = random.random() * total_inv
            cumulative = 0.0
            winner_idx = 0
            for i, w in enumerate(inv):
                cumulative += w
                if r <= cumulative:
                    winner_idx = i
                    break
            _duck.winner_idx = winner_idx
            _duck.phase = "racing"
            await _duck_broadcast({"type": "state", **_duck_snapshot()})
            await asyncio.sleep(_DUCK_RACE_SECS)

            # Payouts
            _duck.phase = "finished"
            if _duck.bets:
                with db_conn() as db:
                    for uid, info in _duck.bets.items():
                        if info["duck_idx"] == winner_idx:
                            payout = round(info["bet"] * _duck.ducks[winner_idx]["mult"])
                            db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (payout, uid))
                            _record_stats(db, uid, duck_won=payout - info["bet"])
                        else:
                            _record_stats(db, uid, duck_lost=info["bet"])
                    db.commit()
            await _duck_broadcast({"type": "state", **_duck_snapshot()})
            await asyncio.sleep(_DUCK_FINISHED_SECS)

        except Exception as _e:
            print(f"[duck_loop] error: {_e}", flush=True)
            await asyncio.sleep(2.0)


@app.websocket("/ws/duck")
async def duck_ws(ws: WebSocket):
    authenticated_user = await _accept_authenticated_websocket(ws)
    if authenticated_user is None:
        return
    _duck.connections.add(ws)
    await ws.send_json({"type": "state", **_duck_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = authenticated_user
            if data.get("user_id") is not None and int(data["user_id"]) != uid:
                await ws.send_json({"type": "error", "message": "User identity mismatch"})
                continue

            if data.get("type") == "bet":
                duck_idx = int(data.get("duck_idx", 0))
                amount = int(data.get("amount", 0))
                if _duck.phase != "waiting":
                    await ws.send_json({"type": "error", "message": "Betting phase has ended"})
                    continue
                if uid in _duck.bets:
                    await ws.send_json({"type": "error", "message": "Already bet this round"})
                    continue
                if duck_idx not in range(4):
                    await ws.send_json({"type": "error", "message": "Invalid duck"})
                    continue
                if amount < 10:
                    await ws.send_json({"type": "error", "message": "Minimum bet is 10 WRK$"})
                    continue
                with db_conn() as db:
                    row = db.execute("SELECT balance, username, full_name FROM economy WHERE user_id = ?", (uid,)).fetchone()
                    if not row:
                        await ws.send_json({"type": "error", "message": "User not found — use the bot first"})
                        continue
                    if row["balance"] < amount:
                        await ws.send_json({"type": "error", "message": f"Insufficient balance"})
                        continue
                    db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (amount, uid))
                    new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()["balance"]
                    public_name = _public_identity(db, uid, f"Player {uid}")
                    db.commit()
                _duck.bets[uid] = {"duck_idx": duck_idx, "bet": amount, "name": public_name}
                if len(_duck.bets) == 1:
                    await _lobby_broadcast({
                        "type": "player_joined",
                        "game": "duck",
                        "game_label": "Duck Racing",
                        "user": _duck.bets[uid]["name"],
                        "user_id": uid,
                    })
                await ws.send_json({"type": "bet_placed", "duck_idx": duck_idx, "bet": amount, "new_balance": new_bal})

    except WebSocketDisconnect:
        _duck.connections.discard(ws)
    except Exception:
        _duck.connections.discard(ws)


# ── Marbles ───────────────────────────────────────────────────────────────────

_MARBLE_OPEN_SECS = 15
_MARBLE_EXTEND_SECS = 10
_MARBLE_LAUNCH_SECS = 6
_MARBLE_FINISHED_SECS = 5
_MARBLE_COLORS = [
  "#ef4444","#3b82f6","#10b981","#f59e0b","#8b5cf6",
  "#ec4899","#06b6d4","#84cc16","#f97316","#6366f1",
  "#14b8a6","#e11d48","#7c3aed","#0284c7","#16a34a",
  "#d97706","#db2777","#0891b2","#65a30d","#4f46e5",
]


class _MarbleState:
    def __init__(self):
        self.phase = "open"
        self.bets: dict[int, dict] = {}   # uid -> {name, wrk, gift_id, gift_value, color, total_value}
        self.pot_wrk: int = 0
        self.pot_gifts: list[int] = []    # gift_instance IDs
        self.winner_id: int | None = None
        self.countdown: float = _MARBLE_OPEN_SECS
        self.connections: set[WebSocket] = set()
        self._color_idx: int = 0

    def next_color(self) -> str:
        c = _MARBLE_COLORS[self._color_idx % len(_MARBLE_COLORS)]
        self._color_idx += 1
        return c


_marble = _MarbleState()


async def _marble_broadcast(msg: dict):
    dead = set()
    for ws in list(_marble.connections):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _marble.connections -= dead


def _marble_snapshot() -> dict:
    total = sum(b["total_value"] for b in _marble.bets.values()) or 1
    return {
        "phase": _marble.phase,
        "countdown": round(_marble.countdown, 1),
        "pot_wrk": _marble.pot_wrk,
        "winner_id": _marble.winner_id,
        "players": [
            {
                "name": v["name"],
                "color": v["color"],
                "total_value": v["total_value"],
                "pct": round(v["total_value"] / total * 100, 1),
                "gift_id": v.get("gift_id"),
            }
            for v in _marble.bets.values()
        ],
    }


async def _marble_loop():
    while True:
        try:
            # Reset
            _marble.phase = "open"
            _marble.bets = {}
            _marble.pot_wrk = 0
            _marble.pot_gifts = []
            _marble.winner_id = None
            _marble._color_idx = 0
            deadline = asyncio.get_running_loop().time() + _MARBLE_OPEN_SECS
            extended = False

            while True:
                _marble.countdown = max(0.0, deadline - asyncio.get_running_loop().time())
                await _marble_broadcast({"type": "state", **_marble_snapshot()})
                if _marble.countdown <= 0:
                    if len(_marble.bets) < 2 and not extended:
                        # Extend once
                        extended = True
                        deadline = asyncio.get_running_loop().time() + _MARBLE_EXTEND_SECS
                    elif len(_marble.bets) < 2:
                        # Refund the lone player and restart
                        if _marble.bets:
                            uid, info = next(iter(_marble.bets.items()))
                            with db_conn() as db:
                                db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (info["wrk"], uid))
                                if info.get("gift_id"):
                                    db.execute("UPDATE gift_instances SET owner_id = ?, staked = 0 WHERE id = ?", (uid, info["gift_id"]))
                                db.commit()
                        await _marble_broadcast({"type": "refund", "message": "Not enough players — refunded"})
                        await asyncio.sleep(3.0)
                        break
                    else:
                        break
                await asyncio.sleep(0.5)

            if len(_marble.bets) < 2:
                continue

            # Pick winner proportionally
            total = sum(b["total_value"] for b in _marble.bets.values())
            roll = random.randint(0, total - 1)
            cumulative = 0
            winner_id = None
            for uid, b in _marble.bets.items():
                cumulative += b["total_value"]
                if roll < cumulative:
                    winner_id = uid
                    break
            _marble.winner_id = winner_id
            _marble.phase = "launching"
            await _marble_broadcast({"type": "state", **_marble_snapshot()})
            await asyncio.sleep(_MARBLE_LAUNCH_SECS)

            # Pay out
            _marble.phase = "finished"
            with db_conn() as db:
                db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (_marble.pot_wrk, winner_id))
                for gid in _marble.pot_gifts:
                    db.execute("UPDATE gift_instances SET owner_id = ?, staked = 0 WHERE id = ?", (winner_id, gid))
                # Record stats
                winner_bet = _marble.bets[winner_id]["wrk"] if winner_id in _marble.bets else 0
                for uid, b in _marble.bets.items():
                    if uid == winner_id:
                        _record_stats(db, uid, marbles_won=max(0, _marble.pot_wrk - winner_bet))
                    else:
                        _record_stats(db, uid, marbles_lost=b["wrk"])
                db.commit()
            await _marble_broadcast({"type": "state", **_marble_snapshot()})
            await asyncio.sleep(_MARBLE_FINISHED_SECS)

        except Exception:
            await asyncio.sleep(2.0)


@app.websocket("/ws/marbles")
async def marbles_ws(ws: WebSocket):
    authenticated_user = await _accept_authenticated_websocket(ws)
    if authenticated_user is None:
        return
    _marble.connections.add(ws)
    await ws.send_json({"type": "state", **_marble_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = authenticated_user
            if data.get("user_id") is not None and int(data["user_id"]) != uid:
                await ws.send_json({"type": "error", "message": "User identity mismatch"})
                continue

            if data.get("type") == "bet":
                if _marble.phase != "open":
                    await ws.send_json({"type": "error", "message": "Betting is closed"})
                    continue
                if uid in _marble.bets:
                    await ws.send_json({"type": "error", "message": "Already in this round"})
                    continue

                gift_id = data.get("gift_id")
                amount = int(data.get("amount", 0))

                with db_conn() as db:
                    row = db.execute("SELECT balance, username, full_name FROM economy WHERE user_id = ?", (uid,)).fetchone()
                    if not row:
                        await ws.send_json({"type": "error", "message": "User not found"})
                        continue

                    if gift_id:
                        # Gift bet — get market value
                        gift_row = db.execute(
                            "SELECT gi.id, gm.tier FROM gift_instances gi "
                            "JOIN gift_models gm ON gm.id = gi.model_id "
                            "WHERE gi.id = ? AND gi.owner_id = ? AND gi.staked = 0",
                            (gift_id, uid)
                        ).fetchone()
                        if not gift_row:
                            await ws.send_json({"type": "error", "message": "Gift not found in your inventory"})
                            continue
                        price_row = db.execute(
                            "SELECT price FROM gift_prices WHERE tier = ? ORDER BY updated_at DESC LIMIT 1",
                            (gift_row["tier"],)
                        ).fetchone()
                        gift_value = price_row["price"] if price_row else 10000
                        db.execute("UPDATE gift_instances SET owner_id = NULL, staked = 1 WHERE id = ?", (gift_id,))
                        new_bal = row["balance"]
                        total_value = gift_value
                        _marble.pot_gifts.append(gift_id)
                        public_name = _public_identity(db, uid, f"Player {uid}")
                        bet_entry = {"name": public_name, "wrk": 0, "gift_id": gift_id, "gift_value": gift_value, "color": _marble.next_color(), "total_value": total_value}
                    else:
                        if amount < 100:
                            await ws.send_json({"type": "error", "message": "Minimum bet is 100 WRK$"})
                            continue
                        if row["balance"] < amount:
                            await ws.send_json({"type": "error", "message": "Insufficient balance"})
                            continue
                        db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (amount, uid))
                        new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()["balance"]
                        _marble.pot_wrk += amount
                        total_value = amount
                        public_name = _public_identity(db, uid, f"Player {uid}")
                        bet_entry = {"name": public_name, "wrk": amount, "gift_id": None, "gift_value": 0, "color": _marble.next_color(), "total_value": total_value}
                    db.commit()

                _marble.bets[uid] = bet_entry
                if len(_marble.bets) == 1:
                    await _lobby_broadcast({
                        "type": "player_joined",
                        "game": "marbles",
                        "game_label": "Marbles",
                        "user": bet_entry["name"],
                        "user_id": uid,
                    })
                await ws.send_json({"type": "bet_placed", "new_balance": new_bal, "color": bet_entry["color"]})
                await _marble_broadcast({"type": "state", **_marble_snapshot()})

    except WebSocketDisconnect:
        _marble.connections.discard(ws)
    except Exception:
        _marble.connections.discard(ws)


# ── Live Blackjack ────────────────────────────────────────────────────────────

_LBJ_BETTING_SECS = 15
_LBJ_TURN_SECS = 30
_LBJ_RESULTS_SECS = 5

def _lbj_fresh_deck() -> list[str]:
    suits = ['♠','♥','♦','♣']
    ranks = ['A','2','3','4','5','6','7','8','9','10','J','Q','K']
    deck = [r+s for s in suits for r in ranks]
    random.shuffle(deck)
    return deck

def _lbj_card_value(card: str) -> int:
    r = card[:-1]
    if r in ('J','Q','K'): return 10
    if r == 'A': return 11
    return int(r)

def _lbj_hand_value(hand: list[str]) -> int:
    total = sum(_lbj_card_value(c) for c in hand)
    aces = sum(1 for c in hand if c[:-1] == 'A')
    while total > 21 and aces:
        total -= 10; aces -= 1
    return total

def _lbj_is_blackjack(hand: list[str]) -> bool:
    return len(hand) == 2 and _lbj_hand_value(hand) == 21


class _LiveBJState:
    def __init__(self):
        self.phase = "waiting"
        self.seats: list[dict] = []   # [{user_id, name, bet, hand, status, doubled}]
        self.dealer_hand: list[str] = []
        self.dealer_hole_shown: bool = False
        self.deck: list[str] = []
        self.current_seat: int = 0
        self.turn_deadline: float = 0.0
        self.connections: dict[WebSocket, int | None] = {}  # ws -> uid
        self.countdown: float = _LBJ_BETTING_SECS

    def seat_for(self, uid: int) -> dict | None:
        return next((s for s in self.seats if s["user_id"] == uid), None)


_livebj = _LiveBJState()


async def _livebj_broadcast_state():
    """Send personalized state snapshots — each player sees their own cards."""
    dead = set()
    for ws, uid in list(_livebj.connections.items()):
        try:
            await ws.send_json({"type": "state", **_livebj_snapshot(uid)})
        except Exception:
            dead.add(ws)
    for ws in dead:
        del _livebj.connections[ws]


async def _livebj_broadcast(msg: dict):
    dead = set()
    for ws in list(_livebj.connections.keys()):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    for ws in dead:
        del _livebj.connections[ws]


def _livebj_snapshot(for_uid: int | None = None) -> dict:
    seats_out = []
    for i, s in enumerate(_livebj.seats):
        seat_copy = {k: v for k, v in s.items() if k != "hand"}
        # Only reveal hand to the owner, or at showdown
        if s["user_id"] == for_uid or _livebj.dealer_hole_shown:
            seat_copy["hand"] = s["hand"]
        else:
            seat_copy["hand"] = ["🂠"] * len(s["hand"])
        seat_copy["value"] = _lbj_hand_value(s["hand"]) if (s["user_id"] == for_uid or _livebj.dealer_hole_shown) else None
        seat_copy["is_turn"] = (i == _livebj.current_seat and _livebj.phase == "player_turns")
        seats_out.append(seat_copy)
    dealer_display = _livebj.dealer_hand if _livebj.dealer_hole_shown else ([_livebj.dealer_hand[0], "🂠"] if _livebj.dealer_hand else [])
    return {
        "phase": _livebj.phase,
        "countdown": round(_livebj.countdown, 1),
        "seats": seats_out,
        "dealer_hand": dealer_display,
        "dealer_value": _lbj_hand_value(_livebj.dealer_hand) if _livebj.dealer_hole_shown else None,
        "current_seat": _livebj.current_seat,
    }


async def _livebj_loop():
    while True:
        try:
            # Betting phase
            _livebj.phase = "waiting"
            _livebj.seats = []
            _livebj.dealer_hand = []
            _livebj.dealer_hole_shown = False
            _livebj.current_seat = 0
            deadline = asyncio.get_running_loop().time() + _LBJ_BETTING_SECS
            while True:
                _livebj.countdown = max(0.0, deadline - asyncio.get_running_loop().time())
                await _livebj_broadcast_state()
                if _livebj.countdown <= 0:
                    break
                await asyncio.sleep(0.5)

            if not _livebj.seats:
                await asyncio.sleep(2.0)
                continue

            if len(_livebj.seats) == 1:
                seat = _livebj.seats[0]
                with db_conn() as db:
                    db.execute(
                        "UPDATE economy SET balance = balance + ? WHERE user_id = ?",
                        (seat["bet"], seat["user_id"])
                    )
                    db.commit()
                await _livebj_broadcast({
                    "type": "solo_refund",
                    "message": "Round cancelled — need at least 2 players. Bet refunded.",
                    "bet": seat["bet"]
                })
                _livebj.seats = []
                await asyncio.sleep(2.0)
                continue

            # Deal
            _livebj.phase = "dealing"
            _livebj.deck = _lbj_fresh_deck()
            for seat in _livebj.seats:
                seat["hand"] = [_livebj.deck.pop(), _livebj.deck.pop()]
                seat["status"] = "playing"
                seat["doubled"] = False
            _livebj.dealer_hand = [_livebj.deck.pop(), _livebj.deck.pop()]
            await _livebj_broadcast_state()
            await asyncio.sleep(1.0)

            # Player turns
            _livebj.phase = "player_turns"
            for i, seat in enumerate(_livebj.seats):
                _livebj.current_seat = i
                if _lbj_is_blackjack(seat["hand"]):
                    seat["status"] = "blackjack"
                    await _livebj_broadcast_state()
                    await asyncio.sleep(1.0)
                    continue
                turn_deadline = asyncio.get_running_loop().time() + _LBJ_TURN_SECS
                while seat["status"] == "playing":
                    remaining = turn_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        seat["status"] = "stood"
                        break
                    _livebj.countdown = remaining
                    await _livebj_broadcast_state()
                    await asyncio.sleep(0.5)

            # Dealer
            _livebj.phase = "dealer"
            _livebj.dealer_hole_shown = True
            while _lbj_hand_value(_livebj.dealer_hand) < 17:
                _livebj.dealer_hand.append(_livebj.deck.pop())
            await _livebj_broadcast_state()
            await asyncio.sleep(1.5)

            # Results
            _livebj.phase = "results"
            dealer_val = _lbj_hand_value(_livebj.dealer_hand)
            dealer_bust = dealer_val > 21
            with db_conn() as db:
                for seat in _livebj.seats:
                    pval = _lbj_hand_value(seat["hand"])
                    bet = seat["bet"]
                    if seat["status"] == "blackjack":
                        payout = int(bet * 2.5)
                        seat["result"] = "blackjack"
                    elif seat["status"] == "bust":
                        payout = 0
                        seat["result"] = "bust"
                    elif dealer_bust or pval > dealer_val:
                        payout = bet * 2
                        seat["result"] = "win"
                    elif pval == dealer_val:
                        payout = bet
                        seat["result"] = "push"
                    else:
                        payout = 0
                        seat["result"] = "lose"
                    if payout:
                        db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (payout, seat["user_id"]))
                    _record_stats(db, seat["user_id"], livebj_won=max(0, payout - bet), livebj_lost=bet if payout == 0 else 0)
                db.commit()
            await _livebj_broadcast_state()
            await asyncio.sleep(_LBJ_RESULTS_SECS)

        except Exception:
            await asyncio.sleep(2.0)


@app.websocket("/ws/livebj")
async def livebj_ws(ws: WebSocket):
    authenticated_user = await _accept_authenticated_websocket(ws)
    if authenticated_user is None:
        return
    _livebj.connections[ws] = None
    uid_ref = [None]
    await ws.send_json({"type": "state", **_livebj_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = authenticated_user
            if data.get("user_id") is not None and int(data["user_id"]) != uid:
                await ws.send_json({"type": "error", "message": "User identity mismatch"})
                continue
            uid_ref[0] = uid
            _livebj.connections[ws] = uid

            if data.get("type") == "join":
                if _livebj.phase != "waiting":
                    await ws.send_json({"type": "error", "message": "Round in progress"})
                    continue
                if len(_livebj.seats) >= 6:
                    await ws.send_json({"type": "error", "message": "Table full (6 players max)"})
                    continue
                if _livebj.seat_for(uid):
                    await ws.send_json({"type": "error", "message": "Already seated"})
                    continue
                bet = int(data.get("bet", 0))
                if bet < 10:
                    await ws.send_json({"type": "error", "message": "Minimum bet is 10 WRK$"})
                    continue
                with db_conn() as db:
                    row = db.execute("SELECT balance, username, full_name FROM economy WHERE user_id = ?", (uid,)).fetchone()
                    if not row or row["balance"] < bet:
                        await ws.send_json({"type": "error", "message": "Insufficient balance"})
                        continue
                    db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (bet, uid))
                    new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()["balance"]
                    public_name = _public_identity(db, uid, f"Player {uid}")
                    db.commit()
                _livebj.seats.append({"user_id": uid, "name": public_name, "bet": bet, "hand": [], "status": "waiting", "doubled": False})
                if len(_livebj.seats) == 1:
                    await _lobby_broadcast({
                        "type": "player_joined",
                        "game": "livebj",
                        "game_label": "Live Blackjack",
                        "user": _livebj.seats[-1]["name"],
                        "user_id": uid,
                    })
                await ws.send_json({"type": "joined", "bet": bet, "new_balance": new_bal})
                await _livebj_broadcast_state()

            elif data.get("type") in ("hit", "stand", "double"):
                seat = _livebj.seat_for(uid)
                if not seat or seat["status"] != "playing" or _livebj.phase != "player_turns":
                    await ws.send_json({"type": "error", "message": "Not your turn"})
                    continue
                if _livebj.seats[_livebj.current_seat]["user_id"] != uid:
                    await ws.send_json({"type": "error", "message": "Not your turn"})
                    continue
                action = data["type"]
                if action == "double":
                    # Check balance before doubling
                    with db_conn() as db:
                        row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()
                        if not row or row["balance"] < seat["bet"]:
                            await ws.send_json({"type": "error", "message": "Insufficient balance to double"})
                            continue
                        db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (seat["bet"], uid))
                        db.commit()
                    seat["bet"] *= 2
                    seat["doubled"] = True
                    seat["hand"].append(_livebj.deck.pop())
                    if _lbj_hand_value(seat["hand"]) > 21:
                        seat["status"] = "bust"
                    else:
                        seat["status"] = "stood"
                elif action == "hit":
                    seat["hand"].append(_livebj.deck.pop())
                    if _lbj_hand_value(seat["hand"]) > 21:
                        seat["status"] = "bust"
                if action == "stand":
                    seat["status"] = "stood"
                await _livebj_broadcast_state()

    except WebSocketDisconnect:
        _livebj.connections.pop(ws, None)
    except Exception:
        _livebj.connections.pop(ws, None)


# ── Texas Hold'Em Poker ───────────────────────────────────────────────────────

_POKER_BUYIN = 10_000
_POKER_SMALL_BLIND = 500
_POKER_BIG_BLIND = 1_000
_POKER_TURN_SECS = 30
_POKER_RESULTS_SECS = 8

def _poker_fresh_deck() -> list[str]:
    suits = ['♠','♥','♦','♣']
    ranks = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']
    deck = [r+s for s in suits for r in ranks]
    random.shuffle(deck)
    return deck

def _card_rank_val(card: str) -> int:
    r = card[:-1]
    order = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':10,'J':11,'Q':12,'K':13,'A':14}
    return order.get(r, 0)

def _evaluate_hand(cards: list[str]) -> tuple:
    """Return a comparable tuple (rank, tiebreakers) for best 5 from 7 cards. Higher = better."""
    from itertools import combinations
    best = None
    for combo in combinations(cards, 5):
        score = _score_5(list(combo))
        if best is None or score > best:
            best = score
    return best

def _score_5(cards: list[str]) -> tuple:
    ranks = sorted([_card_rank_val(c) for c in cards], reverse=True)
    suits = [c[-1] for c in cards]
    is_flush = len(set(suits)) == 1
    is_straight = (ranks == list(range(ranks[0], ranks[0]-5, -1))) or ranks == [14,5,4,3,2]
    if is_straight and ranks == [14,5,4,3,2]: ranks = [5,4,3,2,1]  # wheel: A acts as 1
    from collections import Counter
    cnt = Counter(ranks)
    freq = sorted(cnt.values(), reverse=True)
    uniq = sorted(cnt.keys(), key=lambda r: (cnt[r], r), reverse=True)

    if is_straight and is_flush: return (8, ranks)
    if freq == [4,1]:            return (7, uniq)
    if freq == [3,2]:            return (6, uniq)
    if is_flush:                 return (5, ranks)
    if is_straight:              return (4, ranks)
    if freq[0] == 3:             return (3, uniq)
    if freq[:2] == [2,2]:        return (2, uniq)
    if freq[0] == 2:             return (1, uniq)
    return (0, ranks)


class _PokerState:
    def __init__(self):
        self.phase = "lobby"
        self.seats: list[dict] = []  # [{user_id, name, chips, hole_cards, status, current_bet}]
        self.community: list[str] = []
        self.pot: int = 0
        self.deck: list[str] = []
        self.current_seat: int = 0
        self.dealer_btn: int = 0
        self.min_raise: int = _POKER_BIG_BLIND
        self.current_bet: int = 0
        self.turn_deadline: float = 0.0
        self.connections: dict[int, WebSocket] = {}  # uid -> ws
        self.countdown: float = 0.0

    def active_seats(self) -> list[dict]:
        return [s for s in self.seats if s["status"] not in ("folded","out")]

    def seat_for(self, uid: int) -> dict | None:
        return next((s for s in self.seats if s["user_id"] == uid), None)


_poker = _PokerState()


async def _poker_broadcast(msg: dict, exclude_uid: int | None = None):
    dead = []
    for uid, ws in list(_poker.connections.items()):
        if uid == exclude_uid:
            continue
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(uid)
    for uid in dead:
        _poker.connections.pop(uid, None)


async def _poker_send(uid: int, msg: dict):
    ws = _poker.connections.get(uid)
    if ws:
        try:
            await ws.send_json(msg)
        except Exception:
            _poker.connections.pop(uid, None)


def _poker_snapshot(for_uid: int | None = None) -> dict:
    seats_out = []
    for i, s in enumerate(_poker.seats):
        sc = {k: v for k, v in s.items() if k not in ("hole_cards", "_acted", "_raised")}
        sc["hole_cards"] = s["hole_cards"] if (s["user_id"] == for_uid or _poker.phase == "showdown") else ["🂠","🂠"]
        sc["is_turn"] = (i == _poker.current_seat and _poker.phase in ("pre_flop","flop","turn","river"))
        seats_out.append(sc)
    return {
        "phase": _poker.phase,
        "seats": seats_out,
        "community": _poker.community,
        "pot": _poker.pot,
        "current_bet": _poker.current_bet,
        "min_raise": _poker.min_raise,
        "current_seat": _poker.current_seat,
        "countdown": round(_poker.countdown, 1),
    }


async def _poker_betting_round():
    """Run one betting round, cycling through active players until action closes."""
    active = _poker.active_seats()
    if len(active) <= 1:
        return
    acted = set()
    last_raiser = None
    while True:
        active = _poker.active_seats()
        if len(active) <= 1:
            break
        seat = _poker.seats[_poker.current_seat]
        if seat["status"] in ("folded","out","all_in"):
            _poker.current_seat = (_poker.current_seat + 1) % len(_poker.seats)
            continue
        uid = seat["user_id"]
        # Round complete if everyone who can act has acted and no pending raises
        if uid in acted and uid != last_raiser:
            break
        turn_deadline = asyncio.get_running_loop().time() + _POKER_TURN_SECS
        while True:
            _poker.countdown = max(0.0, turn_deadline - asyncio.get_running_loop().time())
            await _poker_send(uid, {"type": "state", **_poker_snapshot(uid)})
            await _poker_broadcast({"type": "state", **_poker_snapshot()}, exclude_uid=uid)
            if _poker.countdown <= 0:
                # Auto-fold
                seat["status"] = "folded"
                acted.add(uid)
                break
            await asyncio.sleep(0.5)
            # Check if seat acted (status changed or bet changed)
            if seat.get("_acted"):
                seat.pop("_acted", None)
                acted.add(uid)
                if seat.get("_raised"):
                    last_raiser = uid
                    seat.pop("_raised", None)
                    acted = {uid}  # reset — others need to respond
                break
        _poker.current_seat = (_poker.current_seat + 1) % len(_poker.seats)


async def _poker_loop():
    while True:
        try:
            _poker.phase = "waiting"
            await _poker_broadcast({"type": "state", **_poker_snapshot()})
            if len(_poker.seats) < 2:
                await asyncio.sleep(2.0)
                continue

            # Post blinds
            _poker.community = []
            _poker.pot = 0
            _poker.deck = _poker_fresh_deck()
            _poker.current_bet = _POKER_BIG_BLIND
            _poker.min_raise = _POKER_BIG_BLIND
            for seat in _poker.seats:
                seat["hole_cards"] = []
                seat["current_bet"] = 0
                seat["status"] = "active"

            sb_idx = (_poker.dealer_btn + 1) % len(_poker.seats)
            bb_idx = (_poker.dealer_btn + 2) % len(_poker.seats)
            for idx, blind in [(sb_idx, _POKER_SMALL_BLIND), (bb_idx, _POKER_BIG_BLIND)]:
                seat = _poker.seats[idx]
                paid = min(blind, seat["chips"])
                seat["chips"] -= paid
                seat["current_bet"] = paid
                _poker.pot += paid

            # Deal hole cards
            _poker.phase = "pre_flop"
            for seat in _poker.seats:
                seat["hole_cards"] = [_poker.deck.pop(), _poker.deck.pop()]
            for seat in _poker.seats:
                await _poker_send(seat["user_id"], {"type": "state", **_poker_snapshot(seat["user_id"])})

            _poker.current_seat = (bb_idx + 1) % len(_poker.seats)
            await _poker_betting_round()

            # Flop
            _poker.phase = "flop"
            _poker.community = [_poker.deck.pop() for _ in range(3)]
            _poker.current_bet = 0
            for seat in _poker.seats: seat["current_bet"] = 0
            _poker.current_seat = sb_idx
            await _poker_broadcast({"type": "state", **_poker_snapshot()})
            await _poker_betting_round()

            # Turn
            _poker.phase = "turn"
            _poker.community.append(_poker.deck.pop())
            _poker.current_bet = 0
            for seat in _poker.seats: seat["current_bet"] = 0
            _poker.current_seat = sb_idx
            await _poker_broadcast({"type": "state", **_poker_snapshot()})
            await _poker_betting_round()

            # River
            _poker.phase = "river"
            _poker.community.append(_poker.deck.pop())
            _poker.current_bet = 0
            for seat in _poker.seats: seat["current_bet"] = 0
            _poker.current_seat = sb_idx
            await _poker_broadcast({"type": "state", **_poker_snapshot()})
            await _poker_betting_round()

            # Showdown
            _poker.phase = "showdown"
            active = _poker.active_seats()
            winner = None
            if len(active) == 1:
                winner = active[0]
            else:
                best_score = None
                for seat in active:
                    score = _evaluate_hand(seat["hole_cards"] + _poker.community)
                    if best_score is None or score > best_score:
                        best_score = score
                        winner = seat
            if winner:
                winner["chips"] += _poker.pot
                with db_conn() as db:
                    for seat in _poker.seats:
                        profit = seat["chips"] - _POKER_BUYIN
                        if profit > 0:
                            _record_stats(db, seat["user_id"], poker_won=profit)
                        else:
                            _record_stats(db, seat["user_id"], poker_lost=abs(profit))
                        # Return chips to wallet (buy-in already deducted at join)
                        if seat["chips"] > 0:
                            db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?",
                                       (seat["chips"], seat["user_id"]))
                    db.commit()
            await _poker_broadcast({"type": "state", **_poker_snapshot()})
            await asyncio.sleep(_POKER_RESULTS_SECS)

            # Remove busted players
            _poker.seats = [s for s in _poker.seats if s["chips"] > 0]
            _poker.dealer_btn = (_poker.dealer_btn + 1) % max(len(_poker.seats), 1)
            _poker.phase = "lobby" if len(_poker.seats) < 2 else "pre_flop"

        except Exception:
            await asyncio.sleep(2.0)


@app.websocket("/ws/poker")
async def poker_ws(ws: WebSocket):
    authenticated_user = await _accept_authenticated_websocket(ws)
    if authenticated_user is None:
        return
    uid_ref = [None]
    await ws.send_json({"type": "state", **_poker_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = authenticated_user
            if data.get("user_id") is not None and int(data["user_id"]) != uid:
                await ws.send_json({"type": "error", "message": "User identity mismatch"})
                continue
            uid_ref[0] = uid

            if data.get("type") == "join":
                if len(_poker.seats) >= 6:
                    await ws.send_json({"type": "error", "message": "Table full"})
                    continue
                if _poker.seat_for(uid):
                    # Reconnect — update ws
                    _poker.connections[uid] = ws
                    await ws.send_json({"type": "state", **_poker_snapshot(uid)})
                    continue
                if _poker.phase not in ("lobby",):
                    await ws.send_json({"type": "error", "message": "Hand in progress — wait for next hand"})
                    continue
                with db_conn() as db:
                    row = db.execute("SELECT balance, username, full_name FROM economy WHERE user_id = ?", (uid,)).fetchone()
                    if not row or row["balance"] < _POKER_BUYIN:
                        await ws.send_json({"type": "error", "message": f"Need {_POKER_BUYIN:,} WRK$ to buy in"})
                        continue
                    db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (_POKER_BUYIN, uid))
                    new_bal = db.execute("SELECT balance FROM economy WHERE user_id = ?", (uid,)).fetchone()["balance"]
                    public_name = _public_identity(db, uid, f"Player {uid}")
                    db.commit()
                _poker.connections[uid] = ws
                _poker.seats.append({"user_id": uid, "name": public_name, "chips": _POKER_BUYIN, "hole_cards": [], "status": "waiting", "current_bet": 0})
                await _lobby_broadcast({
                    "type": "player_joined",
                    "game": "poker",
                    "game_label": "Poker",
                    "user": _poker.seats[-1]["name"],
                    "user_id": uid,
                })
                await ws.send_json({"type": "joined", "chips": _POKER_BUYIN, "new_balance": new_bal})
                await _poker_broadcast({"type": "state", **_poker_snapshot()})

            elif data.get("type") == "leave":
                seat = next((s for s in _poker.seats if s["user_id"] == uid), None)
                if not seat:
                    await ws.send_json({"type": "error", "message": "Not seated"})
                    continue
                if _poker.phase not in ("lobby", "waiting"):
                    await ws.send_json({"type": "error", "message": "Cannot leave during a hand"})
                    continue
                _poker.seats = [s for s in _poker.seats if s["user_id"] != uid]
                _poker.connections.pop(uid, None)
                with db_conn() as db:
                    db.execute(
                        "UPDATE economy SET balance = balance + ? WHERE user_id = ?",
                        (_POKER_BUYIN, uid)
                    )
                    new_bal = db.execute(
                        "SELECT balance FROM economy WHERE user_id = ?", (uid,)
                    ).fetchone()["balance"]
                    db.commit()
                await ws.send_json({"type": "left", "refund": _POKER_BUYIN, "new_balance": new_bal})
                await _poker_broadcast({"type": "state", **_poker_snapshot()})

            elif data.get("type") in ("fold","check","call","raise"):
                seat = _poker.seat_for(uid)
                if not seat or seat.get("_acted"):
                    continue
                if _poker.seats[_poker.current_seat]["user_id"] != uid:
                    await ws.send_json({"type": "error", "message": "Not your turn"})
                    continue
                action = data["type"]
                if action == "fold":
                    seat["status"] = "folded"
                    seat["_acted"] = True
                elif action == "check":
                    if _poker.current_bet > seat["current_bet"]:
                        await ws.send_json({"type": "error", "message": "Cannot check — must call or raise"})
                        continue
                    seat["_acted"] = True
                elif action == "call":
                    amount = min(_poker.current_bet - seat["current_bet"], seat["chips"])
                    seat["chips"] -= amount
                    seat["current_bet"] += amount
                    _poker.pot += amount
                    seat["_acted"] = True
                elif action == "raise":
                    amount = int(data.get("amount", _poker.min_raise))
                    if amount < _poker.min_raise:
                        await ws.send_json({"type": "error", "message": f"Min raise is {_poker.min_raise}"})
                        continue
                    total = _poker.current_bet + amount
                    paid = min(total - seat["current_bet"], seat["chips"])
                    seat["chips"] -= paid
                    _poker.pot += paid
                    seat["current_bet"] += paid
                    _poker.current_bet = seat["current_bet"]
                    _poker.min_raise = amount
                    seat["_acted"] = True
                    seat["_raised"] = True

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        uid = uid_ref[0]
        _poker.connections.pop(uid, None)
        if uid and _poker.phase not in ("lobby", "waiting"):
            seated_ids = [s["user_id"] for s in _poker.seats]
            if uid in seated_ids:
                # Refund the disconnecting player's chips
                dc_seat = next((s for s in _poker.seats if s["user_id"] == uid), None)
                if dc_seat:
                    with db_conn() as db:
                        db.execute(
                            "UPDATE economy SET balance = balance + ? WHERE user_id = ?",
                            (dc_seat.get("chips", 0), uid)
                        )
                        db.commit()
                _poker.seats = [s for s in _poker.seats if s["user_id"] != uid]
                # If fewer than 2 players remain, reset table
                if len(_poker.seats) < 2:
                    with db_conn() as db:
                        for s in _poker.seats:
                            db.execute(
                                "UPDATE economy SET balance = balance + ? WHERE user_id = ?",
                                (s.get("chips", 0), s["user_id"])
                            )
                        db.commit()
                    _poker.seats.clear()
                    _poker.phase = "lobby"
                    _poker.pot = 0
                    _poker.community = []
        await _poker_broadcast({"type": "state", **_poker_snapshot()})


@app.websocket("/ws/lobby")
async def lobby_ws(ws: WebSocket):
    authenticated_user = await _accept_authenticated_websocket(ws)
    if authenticated_user is None:
        return
    _lobby_connections.add(ws)
    await ws.send_json({"type": "lobby_ready", "event_id": _lobby_event_seq})
    try:
        while True:
            message = await ws.receive_text()
            if message == "ping":
                await ws.send_json({"type": "pong"})
    except Exception:
        pass
    finally:
        _lobby_connections.discard(ws)


# ── Serve SPA ─────────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
