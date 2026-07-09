import random
import sqlite3
import sys
import time
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

# ── Work / Jobs ───────────────────────────────────────────────────────────────

_JOBS = [
    (0,    "🧑‍🎓 Crypto Intern",    60,   120),
    (100,  "📈 Degen Trader",       120,  250),
    (300,  "🌾 Yield Farmer",       250,  500),
    (600,  "🔍 On-Chain Analyst",   400,  800),
    (1000, "⚙️ Protocol Dev",       600, 1200),
    (2000, "🦈 Blockchain Shark",   900, 1800),
    (5000, "👑 Blockchain Baron",  1500, 3000),
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


# ── Work / Jobs endpoints ─────────────────────────────────────────────────────

@app.get("/api/work/status/{user_id}")
def work_status(user_id: int):
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
def work_start(req: WorkStartRequest):
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
def work_sync(req: WorkSyncRequest):
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
def work_end(req: WorkEndRequest):
    with db_conn() as db:
        session_row = db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not session_row:
            raise HTTPException(404, "No active shift")
        session = dict(session_row)
        return _collect_shift(db, req.user_id, session["taps"], session["earned"])


# ── Serve SPA ─────────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
