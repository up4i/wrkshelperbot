import random
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

DB_PATH = config.DB_PATH
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="wrk.money mini-app")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


# ── Leaderboard ───────────────────────────────────────────────────────────────

@app.get("/api/leaderboard")
def leaderboard(tab: str = "balance", limit: int = 20):
    with db_conn() as db:
        if tab == "balance":
            rows = db.execute(
                "SELECT user_id, username, full_name, balance, streak "
                "FROM economy ORDER BY balance DESC LIMIT ?", (limit,)
            ).fetchall()
            return [{"rank": i + 1, "user_id": r["user_id"], "name": _display_name(r),
                     "value": r["balance"], "streak": r["streak"]} for i, r in enumerate(rows)]

        if tab == "streak":
            rows = db.execute(
                "SELECT user_id, username, full_name, balance, streak "
                "FROM economy ORDER BY streak DESC LIMIT ?", (limit,)
            ).fetchall()
            return [{"rank": i + 1, "user_id": r["user_id"], "name": _display_name(r),
                     "value": r["streak"], "balance": r["balance"]} for i, r in enumerate(rows)]

        if tab == "gifts":
            rows = db.execute(
                "SELECT e.user_id, e.username, e.full_name, e.balance, "
                "COUNT(gi.id) AS gift_count "
                "FROM economy e "
                "LEFT JOIN gift_instances gi ON gi.owner_id = e.user_id "
                "GROUP BY e.user_id ORDER BY gift_count DESC LIMIT ?", (limit,)
            ).fetchall()
            return [{"rank": i + 1, "user_id": r["user_id"], "name": _display_name(r),
                     "value": r["gift_count"], "balance": r["balance"]} for i, r in enumerate(rows)]

        raise HTTPException(400, "tab must be balance | streak | gifts")


# ── Profile ───────────────────────────────────────────────────────────────────

def _load_profile(db, user_id: int) -> dict:
    row = db.execute(
        "SELECT user_id, username, full_name, balance, streak, last_daily "
        "FROM economy WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "User not found")

    gifts = db.execute(
        "SELECT gi.id, gi.gift_number, gi.background, gi.acquired_at, "
        "gm.model_name, gm.model_emoji, gm.tier, gm.collection "
        "FROM gift_instances gi JOIN gift_models gm ON gm.id = gi.model_id "
        "WHERE gi.owner_id = ? ORDER BY gi.acquired_at DESC LIMIT 20", (user_id,)
    ).fetchall()

    balance_rank = db.execute(
        "SELECT COUNT(*) + 1 FROM economy WHERE balance > ?", (row["balance"],)
    ).fetchone()[0]
    streak_rank = db.execute(
        "SELECT COUNT(*) + 1 FROM economy WHERE streak > ?", (row["streak"],)
    ).fetchone()[0]
    gift_count = db.execute(
        "SELECT COUNT(*) FROM gift_instances WHERE owner_id = ?", (user_id,)
    ).fetchone()[0]
    gift_rank = db.execute(
        "SELECT COUNT(*) + 1 FROM ("
        "SELECT owner_id, COUNT(*) AS c FROM gift_instances "
        "WHERE owner_id IS NOT NULL GROUP BY owner_id HAVING c > ?) ", (gift_count,)
    ).fetchone()[0]

    return {
        "user_id": row["user_id"],
        "name": _display_name(row),
        "username": row["username"],
        "balance": row["balance"],
        "streak": row["streak"],
        "last_daily": row["last_daily"],
        "balance_rank": balance_rank,
        "streak_rank": streak_rank,
        "gift_count": gift_count,
        "gift_rank": gift_rank,
        "gifts": [dict(g) for g in gifts],
    }


@app.get("/api/profile/{user_id}")
def profile_by_id(user_id: int):
    with db_conn() as db:
        return _load_profile(db, user_id)


@app.get("/api/profile/username/{username}")
def profile_by_username(username: str):
    username = username.lstrip("@")
    with db_conn() as db:
        row = db.execute(
            "SELECT user_id FROM economy WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Username not found")
        return _load_profile(db, row["user_id"])


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    with db_conn() as db:
        users = db.execute("SELECT COUNT(*) FROM economy").fetchone()[0]
        total_wrk = db.execute("SELECT COALESCE(SUM(balance), 0) FROM economy").fetchone()[0]
        gifts_owned = db.execute(
            "SELECT COUNT(*) FROM gift_instances WHERE owner_id IS NOT NULL"
        ).fetchone()[0]
        top_balance = db.execute(
            "SELECT COALESCE(MAX(balance), 0) FROM economy"
        ).fetchone()[0]
        return {"users": users, "total_wrk": total_wrk,
                "gifts_owned": gifts_owned, "top_balance": top_balance}


# ── Games ─────────────────────────────────────────────────────────────────────

_SLOT_SYMBOLS = ["🍒", "🍋", "7️⃣", "💎", "🍀", "⭐"]


def _slot_payout(reels: list[str]) -> tuple[str, int]:
    if reels == ["7️⃣", "7️⃣", "7️⃣"]:
        return "jackpot", 50
    if reels[0] == reels[1] == reels[2]:
        return "three_match", 10
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        return "two_match", 2
    return "no_match", 0


class BetRequest(BaseModel):
    user_id: int
    bet: int


class CoinflipRequest(BaseModel):
    user_id: int
    bet: int
    choice: str


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
def play_slots(req: BetRequest):
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        reels = [random.choice(_SLOT_SYMBOLS) for _ in range(3)]
        kind, mult = _slot_payout(reels)
        delta = req.bet * (mult - 1) if mult > 0 else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        db.commit()
        return {"reels": reels, "result": kind, "multiplier": mult,
                "delta": delta, "new_balance": new_bal}


@app.post("/api/play/coinflip")
def play_coinflip(req: CoinflipRequest):
    if req.choice not in ("heads", "tails"):
        raise HTTPException(400, "choice must be heads or tails")
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        result = random.choice(["heads", "tails"])
        won = result == req.choice
        delta = req.bet if won else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        db.commit()
        return {"result": result, "won": won, "delta": delta, "new_balance": new_bal}


# ── Serve SPA ─────────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
