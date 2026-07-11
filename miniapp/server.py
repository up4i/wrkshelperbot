import asyncio
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
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
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
        username = r["a_username"] or r["e_username"]
        full_name = r["a_full_name"] or r["e_full_name"]
        if username:
            return f"@{username}"
        return full_name or f"User {r['user_id']}"

    with db_conn() as db:
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
                     "value": r["gift_count"], "balance": r["balance"]} for i, r in enumerate(rows)]

        # ── Game stat tabs ────────────────────────────────────────────────────
        _gs_col = {
            "gamble_won":  "gs.slots_won + gs.coinflip_won + gs.blackjack_won + gs.crash_won",
            "gamble_lost": "gs.slots_lost + gs.coinflip_lost + gs.blackjack_lost + gs.crash_lost",
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
                 "value": r["value"]} for i, r in enumerate(rows)]


# ── Profile ───────────────────────────────────────────────────────────────────

def _load_profile(db, user_id: int, gifts_offset: int = 0, gifts_limit: int = 20) -> dict:
    row = db.execute(
        """SELECT e.user_id,
                  e.username  AS e_username,  e.full_name  AS e_full_name,
                  a.username  AS a_username,  a.full_name  AS a_full_name,
                  e.balance, e.streak, e.last_daily, e.pinned_gift_id
           FROM economy e
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
    display   = f"@{username}" if username else (full_name or f"User {user_id}")

    gifts = db.execute(
        "SELECT gi.id, gi.gift_number, gi.background, gi.acquired_at, "
        "gm.model_name, gm.model_emoji, gm.tier, gm.collection, gm.custom_emoji_id "
        "FROM gift_instances gi JOIN gift_models gm ON gm.id = gi.model_id "
        "WHERE gi.owner_id = ? "
        "ORDER BY COALESCE(gi.sort_index, 999999) ASC, gi.acquired_at DESC "
        "LIMIT ? OFFSET ?", (user_id, gifts_limit, gifts_offset)
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

    pinned_gift = None
    if row["pinned_gift_id"]:
        pg = db.execute(
            "SELECT gi.id, gi.gift_number, gi.background, "
            "gm.model_name, gm.model_emoji, gm.custom_emoji_id "
            "FROM gift_instances gi JOIN gift_models gm ON gm.id = gi.model_id "
            "WHERE gi.id = ? AND gi.owner_id = ?",
            (row["pinned_gift_id"], user_id),
        ).fetchone()
        pinned_gift = dict(pg) if pg else None

    return {
        "user_id": row["user_id"],
        "name": display,
        "username": username,
        "balance": row["balance"],
        "streak": row["streak"],
        "last_daily": row["last_daily"],
        "balance_rank": balance_rank,
        "streak_rank": streak_rank,
        "gift_count": gift_count,
        "gift_rank": gift_rank,
        "gifts": [dict(g) for g in gifts],
        "pinned_gift": pinned_gift,
        "pinned_gift_id": row["pinned_gift_id"],
        "has_more": len(gifts) == gifts_limit and (gifts_offset + gifts_limit) < gift_count,
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


# ── Pin gift ─────────────────────────────────────────────────────────────────

class PinGiftRequest(BaseModel):
    user_id: int
    gift_id: int | None = None


class ReorderRequest(BaseModel):
    user_id: int
    gift_ids: list[int]


@app.post("/api/profile/pin")
def pin_gift(req: PinGiftRequest):
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


@app.post("/api/profile/reorder")
def profile_reorder(req: ReorderRequest):
    with db_conn() as db:
        # Verify all gift_ids belong to this user
        placeholders = ",".join("?" * len(req.gift_ids))
        owned = db.execute(
            f"SELECT id FROM gift_instances WHERE id IN ({placeholders}) AND owner_id = ?",
            (*req.gift_ids, req.user_id)
        ).fetchall()
        if len(owned) != len(req.gift_ids):
            raise HTTPException(403, "One or more gifts don't belong to this user")
        for idx, gift_id in enumerate(req.gift_ids):
            db.execute(
                "UPDATE gift_instances SET sort_index = ? WHERE id = ? AND owner_id = ?",
                (idx, gift_id, req.user_id)
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
def avatar_debug(user_id: int):
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

    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        raise HTTPException(403, "initData expired")

    user = json.loads(parsed.get("user", "{}"))
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

    return {"user_id": user_id, "first_name": user.get("first_name", ""),
            "username": user.get("username", ""), "photo_url": photo_url}


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


def _record_stats(db, user_id: int, *,
                  slots_won=0, slots_lost=0,
                  coinflip_won=0, coinflip_lost=0,
                  blackjack_won=0, blackjack_lost=0,
                  crash_won=0, crash_lost=0,
                  crash_mult=0.0,
                  duck_won=0, duck_lost=0,
                  marbles_won=0, marbles_lost=0,
                  livebj_won=0, livebj_lost=0,
                  poker_won=0, poker_lost=0) -> None:
    db.execute(
        """INSERT INTO game_stats
           (user_id, slots_won, slots_lost, coinflip_won, coinflip_lost,
            blackjack_won, blackjack_lost, crash_won, crash_lost, crash_best_mult,
            duck_won, duck_lost, marbles_won, marbles_lost,
            livebj_won, livebj_lost, poker_won, poker_lost)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
               poker_lost      = poker_lost      + excluded.poker_lost""",
        (user_id, slots_won, slots_lost, coinflip_won, coinflip_lost,
         blackjack_won, blackjack_lost, crash_won, crash_lost, crash_mult,
         duck_won, duck_lost, marbles_won, marbles_lost,
         livebj_won, livebj_lost, poker_won, poker_lost),
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
def play_slots(req: BetRequest):
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
    bet_type: str  # red|black|green|odd|even|dozen1|dozen2|dozen3|col1|col2|col3


@app.post("/api/play/roulette")
def play_roulette(req: RouletteRequest):
    valid = {"red","black","green","odd","even","dozen1","dozen2","dozen3","col1","col2","col3"}
    if req.bet_type not in valid:
        raise HTTPException(400, f"bet_type must be one of: {', '.join(sorted(valid))}")
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
        else:  # col3
            won, mult = number > 0 and number % 3 == 0, 3
        delta = req.bet * (mult - 1) if won else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
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


@app.post("/api/play/slider")
def play_slider(req: SliderRequest):
    if not (5 <= req.green_pct <= 95):
        raise HTTPException(400, "green_pct must be between 5 and 95")
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        payout_mult = round(min(19.0, 0.95 / (req.green_pct / 100)), 2)
        landing = random.randint(1, 100)
        won = landing <= req.green_pct
        delta = int(req.bet * (payout_mult - 1)) if won else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
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
    "low":    [0.3, 0.5, 0.8, 1.0, 1.4, 1.0, 0.8, 0.5, 0.3],
    "medium": [0.2, 0.4, 0.6, 1.5, 3.0, 1.5, 0.6, 0.4, 0.2],
    "high":   [0.1, 0.2, 0.5, 2.0, 10.0, 2.0, 0.5, 0.2, 0.1],
}


class PlinkoRequest(BaseModel):
    user_id: int
    bet: int
    risk: str  # low | medium | high


@app.post("/api/play/plinko")
def play_plinko(req: PlinkoRequest):
    if req.risk not in _PLINKO_MULTS:
        raise HTTPException(400, "risk must be low, medium, or high")
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        path = [random.choice([False, True]) for _ in range(_PLINKO_ROWS)]
        slot = sum(1 for p in path if p)   # 0 = all-left, 8 = all-right
        mult = _PLINKO_MULTS[req.risk][slot]
        delta = int(req.bet * mult) - req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        db.commit()
        return {
            "path": path,
            "slot": slot,
            "multiplier": mult,
            "delta": delta,
            "new_balance": new_bal,
        }


# ── Wheel of Fortune ──────────────────────────────────────────────────────────

# 12 segments: 7 bankrupt, 3×1.5, 1×2.0, 1×5.0 → ~95.8% RTP
_WHEEL_SEGS = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 1.5, 1.5, 2.0, 5.0]


class WheelRequest(BaseModel):
    user_id: int
    bet: int


@app.post("/api/play/wheel")
def play_wheel(req: WheelRequest):
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        segment = random.randint(0, 11)
        mult = _WHEEL_SEGS[segment]
        delta = int(req.bet * mult) - req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        db.commit()
        return {
            "segment": segment,
            "multiplier": mult,
            "delta": delta,
            "new_balance": new_bal,
        }


# ── CS Case Opening ───────────────────────────────────────────────────────────

_CASE_PRICE = 75_000

# Cumulative weights (roll 1-100). Each entry: (threshold, tier, wrk_min, wrk_max, gift_tier)
_CASE_LOOT = [
    (55,  "common",    100,    400,  None),
    (80,  "uncommon",  500,   2000,  None),
    (92,  "rare",     2000,  10000,  None),
    (98,  "epic",        0,      0,  "mid"),
    (100, "legendary",   0,      0,  "high"),
]


class CaseRequest(BaseModel):
    user_id: int


@app.post("/api/play/case")
def play_case(req: CaseRequest):
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
        db.commit()
        return {
            "tier": tier_name,
            "wrk_reward": wrk_reward,
            "gift_id": gift_id,
            "gift_name": gift_name,
            "gift_emoji": gift_emoji,
            "new_balance": balance,
        }


# ── High-Low ──────────────────────────────────────────────────────────────────

class HighLowStartRequest(BaseModel):
    user_id: int
    bet: int


class HighLowGuessRequest(BaseModel):
    user_id: int
    direction: str  # "higher" or "lower"


class HighLowCashoutRequest(BaseModel):
    user_id: int


def _card_label(rank: int) -> str:
    return {1: "A", 11: "J", 12: "Q", 13: "K"}.get(rank, str(rank))


@app.post("/api/play/highlow/start")
def highlow_start(req: HighLowStartRequest):
    with db_conn() as db:
        existing = db.execute(
            "SELECT user_id FROM highlow_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if existing:
            raise HTTPException(400, "You already have an active High-Low session")
        bal = _deduct_and_check(db, req.user_id, req.bet)
        new_bal = bal - req.bet
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        card = random.randint(1, 13)
        now = int(time.time())
        db.execute(
            "INSERT INTO highlow_sessions (user_id, bet, current_card, multiplier, started_at) "
            "VALUES (?, ?, ?, 1.0, ?)",
            (req.user_id, req.bet, card, now),
        )
        db.commit()
        return {
            "card": card,
            "card_label": _card_label(card),
            "multiplier": 1.0,
            "new_balance": new_bal,
        }


@app.post("/api/play/highlow/guess")
def highlow_guess(req: HighLowGuessRequest):
    if req.direction not in ("higher", "lower"):
        raise HTTPException(400, "direction must be higher or lower")
    with db_conn() as db:
        sess = db.execute(
            "SELECT * FROM highlow_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not sess:
            raise HTTPException(404, "No active High-Low session — start one first")
        sess = dict(sess)
        next_card = random.randint(1, 13)
        correct = next_card > sess["current_card"] if req.direction == "higher" else next_card < sess["current_card"]

        if correct:
            new_mult = round(sess["multiplier"] * 1.5, 4)
            db.execute(
                "UPDATE highlow_sessions SET current_card = ?, multiplier = ? WHERE user_id = ?",
                (next_card, new_mult, req.user_id),
            )
            db.commit()
            return {
                "result": "correct",
                "next_card": next_card,
                "next_card_label": _card_label(next_card),
                "multiplier": new_mult,
                "potential_win": int(sess["bet"] * new_mult),
            }
        else:
            db.execute("DELETE FROM highlow_sessions WHERE user_id = ?", (req.user_id,))
            db.commit()
            new_bal = db.execute(
                "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
            ).fetchone()["balance"]
            return {
                "result": "wrong",
                "next_card": next_card,
                "next_card_label": _card_label(next_card),
                "lost": sess["bet"],
                "new_balance": new_bal,
            }


@app.post("/api/play/highlow/cashout")
def highlow_cashout(req: HighLowCashoutRequest):
    with db_conn() as db:
        sess = db.execute(
            "SELECT * FROM highlow_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not sess:
            raise HTTPException(404, "No active High-Low session")
        sess = dict(sess)
        winnings = int(sess["bet"] * sess["multiplier"])
        db.execute("DELETE FROM highlow_sessions WHERE user_id = ?", (req.user_id,))
        row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
        new_bal = row["balance"] + winnings
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        db.commit()
        return {"winnings": winnings, "multiplier": sess["multiplier"], "new_balance": new_bal}


@app.get("/api/play/highlow/status/{user_id}")
def highlow_status(user_id: int):
    with db_conn() as db:
        sess = db.execute(
            "SELECT * FROM highlow_sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not sess:
            return {"active": False}
        sess = dict(sess)
        return {
            "active": True,
            "card": sess["current_card"],
            "card_label": _card_label(sess["current_card"]),
            "multiplier": sess["multiplier"],
            "bet": sess["bet"],
            "potential_win": int(sess["bet"] * sess["multiplier"]),
        }


# ── Street Craps ──────────────────────────────────────────────────────────────

class CrapsStartRequest(BaseModel):
    user_id: int
    bet: int


class CrapsRollRequest(BaseModel):
    user_id: int


@app.post("/api/play/craps/start")
def craps_start(req: CrapsStartRequest):
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
def craps_roll(req: CrapsRollRequest):
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
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "win", "winnings": winnings, "new_balance": new_bal}
            elif total in (2, 3, 12):
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
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
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "win", "winnings": winnings, "new_balance": new_bal}
            elif total == 7:
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
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
def craps_status(user_id: int):
    with db_conn() as db:
        sess = db.execute("SELECT * FROM craps_sessions WHERE user_id = ?", (user_id,)).fetchone()
        if not sess:
            return {"active": False}
        return {"active": True, **dict(sess)}


# ── Hack ──────────────────────────────────────────────────────────────────────

_WORDLIST = [
    ("whale",  "Someone who moves markets just by breathing."),
    ("degen",  "Someone who apes into anything with triple-digit APY."),
    ("shill",  "Promoting a token you hold and hope others buy."),
    ("block",  "A bundle of transactions added to the chain."),
    ("miner",  "Solves puzzles to add blocks and earn rewards."),
    ("stake",  "Locking tokens to earn passive income."),
    ("yield",  "The return you earn on a DeFi position."),
    ("token",  "The unit of value native to a blockchain."),
    ("alpha",  "Trading before the crowd catches on. Being early is everything."),
    ("chart",  "Where every degen spends half their waking hours."),
    ("trade",  "Buy low, sell high. Simple in theory."),
    ("vault",  "Where DeFi stores your funds. Hopefully."),
    ("chain",  "The backbone. It's in the name."),
    ("proof",  "The mechanism that keeps a blockchain honest."),
    ("audit",  "When a dev firm checks if the code won't rug you."),
    ("floor",  "The lowest price an NFT collection will sell for."),
    ("layer",  "L2s sit on top of L1s to make things faster and cheaper."),
    ("short",  "Betting the price goes down. High risk, high reward."),
    ("crash",  "When the market decides to humble everyone at once."),
    ("rally",  "A sudden surge upward. WAGMI season."),
    ("greed",  "The emotion that buys tops and sells bottoms."),
    ("limit",  "An order that only executes at your chosen price."),
    ("burns",  "Destroying tokens to reduce supply and pump holders."),
    ("runes",  "Bitcoin's answer to tokens. Inscribed, not bridged."),
    ("nodes",  "The machines keeping the network alive and verified."),
    ("pools",  "Where liquidity lives in a DEX. Provide at your own risk."),
    ("proxy",  "A contract that points to another. Used for upgradeable protocols."),
    ("coins",  "The currency of the chain. Not tokens — native coins."),
    ("smart",  "As in contract. The code that runs without humans."),
    ("ratio",  "Risk/reward. The one number degens ignore."),
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
def hack_status(user_id: int):
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
def hack_start(req: HackStartRequest):
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
            max(5_000, int(balance * 0.005)),
            max(15_000, min(int(balance * 0.015), 500_000)),
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
def hack_guess(req: HackGuessRequest):
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

_ROB_COOLDOWN = 900  # 15 minutes


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
def rob_targets(user_id: int, limit: int = 30):
    with db_conn() as db:
        rows = db.execute(
            """SELECT e.user_id,
                      COALESCE(a.full_name, e.full_name, 'User ' || e.user_id) AS name,
                      e.balance
               FROM economy e
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
def rob_attempt(req: RobAttemptRequest):
    if req.user_id == req.target_id:
        raise HTTPException(400, "You can't rob yourself")
    with db_conn() as db:
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
                      COALESCE(a.full_name, e.full_name, 'User ' || e.user_id) AS name
               FROM economy e
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

        success = random.random() < 0.50
        result = _rob_outcome(success, robber_row["balance"], target_row["balance"])
        target_name = target_row["name"]
        robber_display = (
            f"@{robber_row['username']}" if robber_row["username"]
            else robber_row["full_name"]
        )

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
def rob_cooldown_status(user_id: int):
    with db_conn() as db:
        row = db.execute("SELECT last_rob FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        now = int(time.time())
        remaining = max(0, _ROB_COOLDOWN - (now - (row["last_rob"] or 0)))
        return {"cooldown_remaining": remaining}


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


# ── Market ────────────────────────────────────────────────────────────────────

@app.get("/api/market/collections")
def market_collections(tier: str = "low"):
    with db_conn() as db:
        rows = db.execute(
            """SELECT DISTINCT gm.collection FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.owner_id IS NULL AND gm.tier = ?
               ORDER BY gm.collection""",
            (tier,),
        ).fetchall()
    return [r["collection"] for r in rows]


@app.get("/api/market")
def market_listings(tier: str = "low", limit: int = 40, offset: int = 0,
                    search: str = "", background: str = "", collection: str = ""):
    valid_tiers = ("low", "mid", "high")
    if tier not in valid_tiers:
        raise HTTPException(400, "tier must be low | mid | high")
    where = ["gi.owner_id IS NULL", "gm.tier = ?"]
    params: list = [tier]
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
def market_buy(req: MarketBuyRequest):
    with db_conn() as db:
        row = db.execute(
            """SELECT gi.id, gi.gift_number FROM gift_instances gi
               JOIN gift_models gm ON gm.id = gi.model_id
               WHERE gi.owner_id IS NULL AND gm.collection = ? AND gm.model_number = ? AND gi.background = ?
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
        and len(game["hands"]) == 1
        and hand[0][0] == hand[1][0]
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

    total_delta = 0
    results = []
    for i, hand in enumerate(game["hands"]):
        hand_bet = game["bet"] * (2 if game["doubled"][i] else 1)
        pv = _bj_hand_val(hand)
        if pv > 21:
            outcome, delta = "bust", -hand_bet
        elif dealer_val > 21 or pv > dealer_val:
            outcome, delta = "win", hand_bet
        elif pv == dealer_val:
            outcome, delta = "push", 0
        else:
            outcome, delta = "lose", -hand_bet
        results.append({"outcome": outcome, "delta": delta, "player_value": pv, "hand_bet": hand_bet})
        total_delta += delta

    if total_delta != 0:
        db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (total_delta, user_id))
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


@app.get("/api/blackjack/status/{user_id}")
def blackjack_status(user_id: int):
    game = _bj_games.get(user_id)
    if not game:
        return {"active": False}
    with db_conn() as db:
        row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        balance = row["balance"] if row else 0
    return {"active": True, **_bj_playing_state(game, balance)}


@app.post("/api/blackjack/start")
def blackjack_start(req: BlackjackStartRequest):
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
            if c1["rank"] == c2["rank"]:
                red = {"♥", "♦"}
                if c1["suit"] == c2["suit"]:
                    pp_result, pp_mult = "perfect", 6
                elif (c1["suit"] in red) == (c2["suit"] in red):
                    pp_result, pp_mult = "colored", 4
                else:
                    pp_result, pp_mult = "mixed", 3
                pp_delta = req.pp_bet * (pp_mult - 1)
            else:
                pp_result, pp_delta = "none", -req.pp_bet
            balance += pp_delta
            db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))

        if _bj_hand_val(player) == 21:
            winnings = int(req.bet * 1.5)
            balance += winnings
            db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (balance, req.user_id))
            db.commit()
            resp = {
                "status": "blackjack",
                "bet": req.bet,
                "hands": [_bj_fmt(player)],
                "dealer_hand": _bj_fmt(dealer),
                "player_values": [21],
                "dealer_value": _bj_hand_val(dealer),
                "results": [{"outcome": "blackjack", "delta": winnings, "player_value": 21, "hand_bet": req.bet}],
                "total_delta": winnings,
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
def blackjack_action(req: BlackjackActionRequest):
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
            game["doubled"][ci] = True
            hand.append(game["deck"].pop())
            if ci < len(game["hands"]) - 1:
                game["current_hand"] += 1
                return _bj_playing_state(game, balance)
            return _bj_resolve_game(db, req.user_id, game)

        if req.action == "split":
            if len(hand) != 2 or len(game["hands"]) > 1 or balance < game["bet"]:
                raise HTTPException(400, "Can't split now")
            c1, c2 = hand
            game["hands"] = [[c1, game["deck"].pop()], [c2, game["deck"].pop()]]
            game["doubled"] = [False, False]
            game["current_hand"] = 0
            return _bj_playing_state(game, balance)

    return _bj_playing_state(game, balance)  # unreachable but satisfies linter


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


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_crash_loop())
    asyncio.create_task(_duck_loop())
    asyncio.create_task(_marble_loop())
    asyncio.create_task(_livebj_loop())
    asyncio.create_task(_poker_loop())
    with db_conn() as db:
        for col in ("pinned_gift_id INTEGER", "photo_url TEXT"):
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


@app.websocket("/ws/crash")
async def crash_ws(ws: WebSocket):
    await ws.accept()
    _crash.connections.add(ws)
    await ws.send_json({"type": "state", **_crash_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = data.get("user_id")
            if not uid:
                continue
            uid = int(uid)

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
                    name_row = db.execute("SELECT username, full_name FROM economy WHERE user_id = ?", (uid,)).fetchone()
                    _crash.names[uid] = (name_row["username"] or name_row["full_name"] or f"Player {uid}") if name_row else f"Player {uid}"
                    db.commit()
                _crash.bets[uid] = {"bet": amount, "cashed_out": False}
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

        except Exception:
            await asyncio.sleep(2.0)


@app.websocket("/ws/duck")
async def duck_ws(ws: WebSocket):
    await ws.accept()
    _duck.connections.add(ws)
    await ws.send_json({"type": "state", **_duck_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = data.get("user_id")
            if not uid:
                continue
            uid = int(uid)

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
                    db.commit()
                _duck.bets[uid] = {"duck_idx": duck_idx, "bet": amount, "name": row["username"] or row["full_name"] or f"Player {uid}"}
                await ws.send_json({"type": "bet_placed", "duck_idx": duck_idx, "bet": amount, "new_balance": new_bal})

    except WebSocketDisconnect:
        _duck.connections.discard(ws)
    except Exception:
        _duck.connections.discard(ws)


# ── Marbles ───────────────────────────────────────────────────────────────────

_MARBLE_OPEN_SECS = 20
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
                db.commit()
            await _marble_broadcast({"type": "state", **_marble_snapshot()})
            await asyncio.sleep(_MARBLE_FINISHED_SECS)

        except Exception:
            await asyncio.sleep(2.0)


@app.websocket("/ws/marbles")
async def marbles_ws(ws: WebSocket):
    await ws.accept()
    _marble.connections.add(ws)
    await ws.send_json({"type": "state", **_marble_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = data.get("user_id")
            if not uid:
                continue
            uid = int(uid)

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
                        bet_entry = {"name": row["username"] or row["full_name"] or f"Player {uid}", "wrk": 0, "gift_id": gift_id, "gift_value": gift_value, "color": _marble.next_color(), "total_value": total_value}
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
                        bet_entry = {"name": row["username"] or row["full_name"] or f"Player {uid}", "wrk": amount, "gift_id": None, "gift_value": 0, "color": _marble.next_color(), "total_value": total_value}
                    db.commit()

                _marble.bets[uid] = bet_entry
                await ws.send_json({"type": "bet_placed", "new_balance": new_bal, "color": bet_entry["color"]})
                await _marble_broadcast({"type": "state", **_marble_snapshot()})

    except WebSocketDisconnect:
        _marble.connections.discard(ws)
    except Exception:
        _marble.connections.discard(ws)


# ── Live Blackjack ────────────────────────────────────────────────────────────

_LBJ_BETTING_SECS = 10
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
        self.connections: set[WebSocket] = set()
        self.countdown: float = _LBJ_BETTING_SECS

    def seat_for(self, uid: int) -> dict | None:
        return next((s for s in self.seats if s["user_id"] == uid), None)


_livebj = _LiveBJState()


async def _livebj_broadcast(msg: dict):
    dead = set()
    for ws in list(_livebj.connections):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _livebj.connections -= dead


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
                await _livebj_broadcast({"type": "state", **_livebj_snapshot()})
                if _livebj.countdown <= 0:
                    break
                await asyncio.sleep(0.5)

            if not _livebj.seats:
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
            await _livebj_broadcast({"type": "state", **_livebj_snapshot()})
            await asyncio.sleep(1.0)

            # Player turns
            _livebj.phase = "player_turns"
            for i, seat in enumerate(_livebj.seats):
                _livebj.current_seat = i
                if _lbj_is_blackjack(seat["hand"]):
                    seat["status"] = "blackjack"
                    await _livebj_broadcast({"type": "state", **_livebj_snapshot()})
                    await asyncio.sleep(1.0)
                    continue
                turn_deadline = asyncio.get_running_loop().time() + _LBJ_TURN_SECS
                while seat["status"] == "playing":
                    remaining = turn_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        seat["status"] = "stood"
                        break
                    _livebj.countdown = remaining
                    await _livebj_broadcast({"type": "state", **_livebj_snapshot()})
                    await asyncio.sleep(0.5)

            # Dealer
            _livebj.phase = "dealer"
            _livebj.dealer_hole_shown = True
            while _lbj_hand_value(_livebj.dealer_hand) < 17:
                _livebj.dealer_hand.append(_livebj.deck.pop())
            await _livebj_broadcast({"type": "state", **_livebj_snapshot()})
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
            await _livebj_broadcast({"type": "state", **_livebj_snapshot()})
            await asyncio.sleep(_LBJ_RESULTS_SECS)

        except Exception:
            await asyncio.sleep(2.0)


@app.websocket("/ws/livebj")
async def livebj_ws(ws: WebSocket):
    await ws.accept()
    _livebj.connections.add(ws)
    uid_ref = [None]
    await ws.send_json({"type": "state", **_livebj_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = data.get("user_id")
            if not uid:
                continue
            uid = int(uid)
            uid_ref[0] = uid

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
                    db.commit()
                _livebj.seats.append({"user_id": uid, "name": row["username"] or row["full_name"] or f"Player {uid}", "bet": bet, "hand": [], "status": "waiting", "doubled": False})
                await ws.send_json({"type": "joined", "bet": bet, "new_balance": new_bal})
                await _livebj_broadcast({"type": "state", **_livebj_snapshot()})

            elif data.get("type") in ("hit", "stand", "double"):
                seat = _livebj.seat_for(uid)
                if not seat or seat["status"] != "playing" or _livebj.phase != "player_turns":
                    await ws.send_json({"type": "error", "message": "Not your turn"})
                    continue
                if _livebj.seats[_livebj.current_seat]["user_id"] != uid:
                    await ws.send_json({"type": "error", "message": "Not your turn"})
                    continue
                action = data["type"]
                if action in ("hit", "double"):
                    seat["hand"].append(_livebj.deck.pop())
                    if action == "double":
                        # Deduct extra bet
                        with db_conn() as db:
                            db.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (seat["bet"], uid))
                            db.commit()
                        seat["bet"] *= 2
                        seat["doubled"] = True
                    if _lbj_hand_value(seat["hand"]) > 21:
                        seat["status"] = "bust"
                    elif action == "double":
                        seat["status"] = "stood"
                if action == "stand":
                    seat["status"] = "stood"
                await _livebj_broadcast({"type": "state", **_livebj_snapshot()})

    except WebSocketDisconnect:
        _livebj.connections.discard(ws)
    except Exception:
        _livebj.connections.discard(ws)


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
    await ws.accept()
    uid_ref = [None]
    await ws.send_json({"type": "state", **_poker_snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            uid = data.get("user_id")
            if not uid:
                continue
            uid = int(uid)
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
                    db.commit()
                _poker.connections[uid] = ws
                _poker.seats.append({"user_id": uid, "name": row["username"] or row["full_name"] or f"Player {uid}", "chips": _POKER_BUYIN, "hole_cards": [], "status": "waiting", "current_bet": 0})
                await ws.send_json({"type": "joined", "chips": _POKER_BUYIN, "new_balance": new_bal})
                await _poker_broadcast({"type": "state", **_poker_snapshot()})

            elif data.get("type") == "leave":
                seat = _poker.seat_for(uid)
                if seat and _poker.phase == "lobby":
                    with db_conn() as db:
                        db.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (seat["chips"], uid))
                        db.commit()
                    _poker.seats = [s for s in _poker.seats if s["user_id"] != uid]
                    _poker.connections.pop(uid, None)
                    await ws.send_json({"type": "left", "chips_returned": seat["chips"]})
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

    except WebSocketDisconnect:
        uid = uid_ref[0]
        if uid:
            _poker.connections.pop(uid, None)
    except Exception:
        uid = uid_ref[0]
        if uid:
            _poker.connections.pop(uid, None)


# ── Serve SPA ─────────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
