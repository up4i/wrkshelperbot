# Mini-App v2: Jobs System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move work session state from bot in-memory dicts to SQLite so bot `/work` and the mini-app Jobs page share one source of truth, then build a tap-game Jobs page in the mini-app.

**Architecture:** A new `work_sessions` table holds one row per active shift. The bot replaces its `_work_sessions` / `_work_cooldowns` dicts with async DB reads/writes (identical user-facing behavior). The mini-app gets 4 new REST endpoints and a new Jobs page that batches tap state to the server every 5 taps for latency tolerance.

**Tech Stack:** Python 3.14, aiosqlite (bot), sqlite3 sync (FastAPI server), FastAPI, Pydantic v2, vanilla JS (no build step)

---

## File Map

| File | Change |
|---|---|
| `db.py` | Add `work_sessions` to `_SCHEMA`, add to `_migrate`, add 4 async helpers |
| `handlers/economy.py` | Remove `_work_sessions` / `_work_cooldowns` dicts; rewrite `cmd_work`, `work_callback`, `_end_shift` to use DB |
| `miniapp/server.py` | Add `_JOBS` / `_SHIFT_*` constants, `_get_tier_index`, `_job_info`, `_collect_shift`; add 4 endpoints |
| `miniapp/static/index.html` | Add 5th nav tab, Jobs page HTML, CSS, JS state machine |
| `tests/test_work_sessions_db.py` | New: tests for all 4 DB helper functions |

---

## Task 1: work_sessions DB schema + helper functions

**Files:**
- Modify: `db.py` (schema string, `_migrate`, append 4 functions at end)
- Create: `tests/test_work_sessions_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_work_sessions_db.py`:

```python
import time
import pytest
from db import init_db, upsert_wallet, get_work_session, start_work_session, sync_work_session, end_work_session

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")

@pytest.mark.asyncio
async def test_get_work_session_none_when_no_session(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    result = await get_work_session(db_path, 1)
    assert result is None

@pytest.mark.asyncio
async def test_start_work_session_creates_row(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    session = await start_work_session(db_path, 1, tap_count_start=50, job_tier_index=1)
    assert session["user_id"] == 1
    assert session["taps"] == 0
    assert session["earned"] == 0
    assert session["job_tier_index"] == 1
    assert session["tap_count_start"] == 50

@pytest.mark.asyncio
async def test_get_work_session_returns_row_after_start(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    session = await get_work_session(db_path, 1)
    assert session is not None
    assert session["user_id"] == 1

@pytest.mark.asyncio
async def test_sync_work_session_accumulates(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    updated = await sync_work_session(db_path, 1, taps_delta=5, earned_delta=450)
    assert updated["taps"] == 5
    assert updated["earned"] == 450
    updated2 = await sync_work_session(db_path, 1, taps_delta=3, earned_delta=270)
    assert updated2["taps"] == 8
    assert updated2["earned"] == 720

@pytest.mark.asyncio
async def test_sync_work_session_returns_none_for_no_session(db_path):
    await init_db(db_path)
    result = await sync_work_session(db_path, 99, taps_delta=1, earned_delta=100)
    assert result is None

@pytest.mark.asyncio
async def test_end_work_session_returns_final_state_and_deletes(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    await sync_work_session(db_path, 1, taps_delta=10, earned_delta=900)
    final = await end_work_session(db_path, 1)
    assert final["taps"] == 10
    assert final["earned"] == 900
    assert await get_work_session(db_path, 1) is None

@pytest.mark.asyncio
async def test_end_work_session_returns_none_when_no_session(db_path):
    await init_db(db_path)
    result = await end_work_session(db_path, 99)
    assert result is None

@pytest.mark.asyncio
async def test_start_work_session_replaces_existing(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "alice", "Alice")
    await start_work_session(db_path, 1, tap_count_start=0, job_tier_index=0)
    await sync_work_session(db_path, 1, taps_delta=10, earned_delta=900)
    # Starting a new session replaces the old one (INSERT OR REPLACE)
    session2 = await start_work_session(db_path, 1, tap_count_start=100, job_tier_index=1)
    assert session2["taps"] == 0
    assert session2["earned"] == 0
    assert session2["job_tier_index"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -m pytest tests/test_work_sessions_db.py -v 2>&1 | head -30
```

Expected: `ImportError` — `get_work_session` not yet defined.

- [ ] **Step 3: Add work_sessions to _SCHEMA in db.py**

In `db.py`, find the end of the `_SCHEMA` string (just before the closing `"""`). Add this table definition:

```python
CREATE TABLE IF NOT EXISTS work_sessions (
    user_id         INTEGER PRIMARY KEY,
    taps            INTEGER NOT NULL DEFAULT 0,
    earned          INTEGER NOT NULL DEFAULT 0,
    started_at      INTEGER NOT NULL,
    job_tier_index  INTEGER NOT NULL DEFAULT 0,
    tap_count_start INTEGER NOT NULL DEFAULT 0
);
```

The table uses `CREATE TABLE IF NOT EXISTS` so it is safe to add to the schema string — existing DBs will create it on next `init_db` call via `executescript`.

- [ ] **Step 4: Add the 4 async helper functions to db.py**

Append after the `claim_work` function (around line 444):

```python
async def get_work_session(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM work_sessions WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def start_work_session(db_path: str, user_id: int, tap_count_start: int, job_tier_index: int) -> dict:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO work_sessions "
            "(user_id, taps, earned, started_at, job_tier_index, tap_count_start) "
            "VALUES (?, 0, 0, ?, ?, ?)",
            (user_id, now, job_tier_index, tap_count_start),
        )
        await db.commit()
    return {
        "user_id": user_id, "taps": 0, "earned": 0,
        "started_at": now, "job_tier_index": job_tier_index,
        "tap_count_start": tap_count_start,
    }


async def sync_work_session(db_path: str, user_id: int, taps_delta: int, earned_delta: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "UPDATE work_sessions SET taps = taps + ?, earned = earned + ? "
            "WHERE user_id = ? RETURNING *",
            (taps_delta, earned_delta, user_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return dict(row) if row else None


async def end_work_session(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "DELETE FROM work_sessions WHERE user_id = ? RETURNING *", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return dict(row) if row else None
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest tests/test_work_sessions_db.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_work_sessions_db.py
git commit -m "feat: work_sessions table and DB helpers"
```

---

## Task 2: Refactor bot work handler to use DB sessions

**Files:**
- Modify: `handlers/economy.py` (lines ~230–430)

No new test file needed — behavior is identical; existing bot manual testing in Telegram is the acceptance test. The DB helper tests in Task 1 cover the data layer.

- [ ] **Step 1: Remove in-memory session dicts**

In `handlers/economy.py`, delete these two lines (around line 249–250):

```python
_work_sessions: dict[int, dict] = {}   # user_id -> active shift state
_work_cooldowns: dict[int, float] = {} # user_id -> shift-end timestamp
```

- [ ] **Step 2: Rewrite cmd_work**

Replace the entire `cmd_work` function (lines ~290–336) with:

```python
async def cmd_work(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if msg.chat.type != "private":
        await msg.reply_text("💼 Use /work in DMs with me to start your shift!")
        return

    session = await db.get_work_session(config.DB_PATH, user.id)
    if session:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚡ Work", callback_data=f"work:tap:{user.id}"),
            InlineKeyboardButton("🏁 End Shift", callback_data=f"work:end:{user.id}"),
        ]])
        await msg.reply_text(
            "You have an active shift!\n\n" + _shift_message(session),
            parse_mode="Markdown", reply_markup=kb
        )
        return

    wallet = await _ensure_wallet(user, config.DB_PATH)
    now = time.time()
    remaining = _SHIFT_COOLDOWN - (now - (wallet.get("last_work") or 0))
    if remaining > 0:
        m, s = divmod(int(remaining), 60)
        await msg.reply_text(f"⏳ Next shift starts in *{m}m {s}s*.", parse_mode="Markdown")
        return

    tap_count = wallet.get("work_count", 0) or 0
    job = _get_job(tap_count)
    tier_index = _JOBS.index(job)

    session = await db.start_work_session(config.DB_PATH, user.id, tap_count_start=tap_count, job_tier_index=tier_index)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚡ Work", callback_data=f"work:tap:{user.id}"),
        InlineKeyboardButton("🏁 End Shift", callback_data=f"work:end:{user.id}"),
    ]])
    await msg.reply_text(
        "🟢 *Shift started!* Keep tapping ⚡ Work to earn.\n\n"
        + _shift_message(session),
        parse_mode="Markdown", reply_markup=kb
    )
```

- [ ] **Step 3: Update _shift_message to accept dict**

`_shift_message` currently accepts a dict from the old session format. Verify its key access still matches — the new dict from `get_work_session` / `start_work_session` has the same keys (`job` is now stored as `job_tier_index`, so we need to reconstruct the job tuple). Replace `_shift_message`:

```python
def _shift_message(session: dict) -> str:
    job = _JOBS[session["job_tier_index"]]
    _, title, lo, hi = job
    taps = session["taps"]
    earned = session["earned"]
    tap_count = session["tap_count_start"] + taps
    next_tier = _next_job(tap_count)
    promo = (
        f"\n📊 {next_tier[0] - tap_count} taps to unlock {next_tier[1]}"
        if next_tier else "\n👑 Max tier achieved!"
    )
    bar_filled = int(taps / _SHIFT_MAX_TAPS * 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    return (
        f"💼 *{title}*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 Earned: *{earned:,} WRK$*\n"
        f"👆 Taps: {taps}/{_SHIFT_MAX_TAPS}  [{bar}]\n"
        f"⚡ {lo}–{hi} WRK$ per tap"
        f"{promo}"
    )
```

- [ ] **Step 4: Rewrite work_callback**

Replace the entire `work_callback` function (lines ~339–388) with:

```python
async def work_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, action, uid_str = query.data.split(":")
    user_id = int(uid_str)

    if query.from_user.id != user_id:
        await query.answer("Not your shift.", show_alert=True)
        return

    session = await db.get_work_session(config.DB_PATH, user_id)

    # ── tap ──
    if action == "tap":
        if not session:
            await query.answer("No active shift. Use /work to start one.", show_alert=True)
            return

        _, _, lo, hi = _JOBS[session["job_tier_index"]]
        earned_this_tap = random.randint(lo, hi)

        session = await db.sync_work_session(config.DB_PATH, user_id, taps_delta=1, earned_delta=earned_this_tap)
        await query.answer(f"+{earned_this_tap:,} WRK$ 💰")

        if session["taps"] % 5 == 0 or session["taps"] >= _SHIFT_MAX_TAPS:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⚡ Work", callback_data=f"work:tap:{user_id}"),
                InlineKeyboardButton("🏁 End Shift", callback_data=f"work:end:{user_id}"),
            ]])
            try:
                await query.edit_message_text(
                    _shift_message(session), parse_mode="Markdown", reply_markup=kb
                )
            except TelegramError:
                pass

        if session["taps"] >= _SHIFT_MAX_TAPS:
            await _end_shift(query, user_id, session, auto=True)
        return

    # ── end ──
    if action == "end":
        if not session:
            await query.answer("No active shift.", show_alert=True)
            return
        await query.answer()
        await _end_shift(query, user_id, session, auto=False)
```

- [ ] **Step 5: Rewrite _end_shift**

Replace the entire `_end_shift` function (lines ~390–430) with:

```python
async def _end_shift(query, user_id: int, session: dict, auto: bool):
    final = await db.end_work_session(config.DB_PATH, user_id)
    if not final:
        return

    total = final["earned"]
    taps = final["taps"]

    if total == 0:
        await query.edit_message_text("You ended your shift without earning anything. Tap ⚡ next time!")
        return

    new_bal, new_tap_count = await db.claim_work(config.DB_PATH, user_id, total, int(time.time()))

    if taps > 1:
        async with __import__('aiosqlite').connect(config.DB_PATH) as _db:
            await _db.execute(
                "UPDATE economy SET work_count = work_count + ? WHERE user_id = ?",
                (taps - 1, user_id)
            )
            await _db.commit()
        new_tap_count = new_tap_count + (taps - 1)

    old_title = _JOBS[final["job_tier_index"]][1]
    new_title = _get_job(new_tap_count)[1]
    promo_line = f"\n\n🎉 *Promoted to {new_title}!*" if new_title != old_title else ""

    next_tier = _next_job(new_tap_count)
    progress = f"\n📊 {next_tier[0] - new_tap_count} taps to {next_tier[1]}" if next_tier else "\n👑 Max tier!"

    prefix = "⏰ Max taps reached! Shift auto-ended.\n\n" if auto else "🏁 *Shift complete!*\n\n"
    await query.edit_message_text(
        f"{prefix}"
        f"👆 Taps this shift: {taps}\n"
        f"💰 Collected: *{total:,} WRK$*\n"
        f"Balance: {new_bal:,} WRK$"
        f"{promo_line}{progress}",
        parse_mode="Markdown"
    )
```

- [ ] **Step 6: Verify bot starts without error**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -c "from handlers.economy import cmd_work, work_callback; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add handlers/economy.py
git commit -m "refactor: work handler uses DB-backed sessions instead of in-memory dicts"
```

---

## Task 3: Mini-app API endpoints

**Files:**
- Modify: `miniapp/server.py`

- [ ] **Step 1: Add shared constants and helpers near top of server.py**

After the existing `_SLOT_SYMBOLS` constant (around line 158), add:

```python
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
    db.execute(
        "UPDATE economy SET balance = balance + ?, last_work = ?, work_count = work_count + ? WHERE user_id = ?",
        (earned, now, taps, user_id),
    )
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
```

Note: `server.py` uses synchronous `sqlite3` (via `db_conn()`), so `_collect_shift` is synchronous. It does NOT call `db.py`'s async `claim_work` — it writes directly for simplicity and correctness (adds full `taps` count to `work_count` in one shot, no hack needed).

Also add `import time` to the top of `server.py` if not already present.

- [ ] **Step 2: Add Pydantic request models**

After the existing `CoinflipRequest` model (around line 179), add:

```python
class WorkStartRequest(BaseModel):
    user_id: int

class WorkSyncRequest(BaseModel):
    user_id: int
    taps_delta: int
    earned_delta: int

class WorkEndRequest(BaseModel):
    user_id: int
```

- [ ] **Step 3: Add the 4 endpoints**

Add these after the `play_coinflip` endpoint and before the `app.mount` line:

```python
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
        db.execute(
            "UPDATE work_sessions SET taps = ?, earned = ? WHERE user_id = ?",
            (new_taps, new_earned, req.user_id),
        )
        db.commit()
        if new_taps >= _SHIFT_MAX_TAPS:
            result = _collect_shift(db, req.user_id, new_taps, new_earned)
            result["auto_ended"] = True
            return result
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
```

- [ ] **Step 4: Verify server imports cleanly**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -c "from miniapp.server import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Quick smoke test of endpoints**

Start the server in background, hit each endpoint:

```bash
python -m uvicorn miniapp.server:app --host 127.0.0.1 --port 8421 &
sleep 2
# Should 404 (no DB on dev machine)
curl -s http://127.0.0.1:8421/api/work/status/1 | python -m json.tool
# Should be 404 "User not found" — that's correct, no data in dev DB
kill %1
```

Expected: JSON response with `{"detail": "User not found"}` and HTTP 404.

- [ ] **Step 6: Commit**

```bash
git add miniapp/server.py
git commit -m "feat: work/jobs API endpoints in mini-app server"
```

---

## Task 4: Mini-app Jobs UI

**Files:**
- Modify: `miniapp/static/index.html`

This is a single-file SPA. All additions go into the existing file — CSS in the `<style>` block, HTML in `<main>`, a new nav button in `<nav>`, and JS at the bottom of `<script>`.

- [ ] **Step 1: Add CSS for Jobs page**

Inside the `<style>` block, before the closing `</style>`, add:

```css
  /* ── Jobs / Work page ── */
  .job-hero {
    background: linear-gradient(135deg, rgba(139,92,246,.18), rgba(6,182,212,.08));
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 24px 20px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
  }
  .job-title { font-size: 20px; font-weight: 800; }
  .job-earn-range { font-size: 13px; color: var(--muted); }
  .job-next {
    font-size: 12px;
    color: var(--primary);
    background: rgba(139,92,246,.1);
    border-radius: 20px;
    padding: 4px 12px;
  }
  .tier-bar-wrap { width: 100%; background: var(--card2); border-radius: 8px; height: 6px; overflow: hidden; }
  .tier-bar-fill { height: 100%; background: var(--primary); border-radius: 8px; transition: width .4s; }

  .shift-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    margin-bottom: 4px;
  }
  .shift-earned { font-size: 28px; font-weight: 800; color: var(--gold); }
  .shift-taps { font-size: 13px; color: var(--muted); }
  .shift-progress-wrap { background: var(--card2); border-radius: 8px; height: 8px; overflow: hidden; margin-bottom: 16px; }
  .shift-progress-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #06b6d4); border-radius: 8px; transition: width .2s; }

  .tap-zone-wrap { display: flex; justify-content: center; padding: 8px 0 16px; }
  .tap-zone {
    width: 160px; height: 160px;
    border-radius: 50%;
    background: radial-gradient(circle at center, rgba(139,92,246,.25), transparent 70%);
    border: 2px solid var(--primary);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 72px;
    cursor: pointer;
    user-select: none;
    -webkit-tap-highlight-color: transparent;
    transition: transform .08s, box-shadow .08s;
    box-shadow: 0 0 20px rgba(139,92,246,.2);
  }
  .tap-zone:active { transform: scale(0.91); box-shadow: 0 0 40px rgba(139,92,246,.5); }

  .cooldown-card {
    text-align: center;
    padding: 32px 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }
  .cooldown-icon { font-size: 48px; }
  .cooldown-label { font-size: 13px; color: var(--muted); }
  .cooldown-timer { font-size: 36px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--primary); }

  /* floating +X animation */
  .float-earn {
    position: fixed;
    pointer-events: none;
    font-size: 18px;
    font-weight: 800;
    color: var(--gold);
    text-shadow: 0 1px 4px rgba(0,0,0,.6);
    z-index: 999;
    animation: float-earn .9s ease-out forwards;
  }
  @keyframes float-earn {
    0%   { opacity: 1; transform: translateY(0) scale(1); }
    30%  { opacity: 1; transform: translateY(-20px) scale(1.1); }
    100% { opacity: 0; transform: translateY(-70px) scale(.9); }
  }
```

- [ ] **Step 2: Add Jobs page HTML**

In `<main>`, after the closing `</div>` of the PROFILE page (around line 582), add:

```html
    <!-- WORK / JOBS -->
    <div class="page" id="page-work">
      <div>
        <div class="section-title">Work</div>
        <div class="section-sub">Tap to earn — shifts unlock higher-paying jobs</div>
      </div>

      <!-- login wall (same as games) -->
      <div id="workLoginWall" class="card">
        <div class="card-title">Sign in first</div>
        <div class="search-row">
          <input class="input" id="workUserId" placeholder="Your Telegram user ID" type="number">
          <button class="btn" onclick="loginFromWork()">Set</button>
        </div>
        <div class="user-id-hint" style="margin-top:10px">
          Send <strong>/id</strong> in the bot to get your ID.
        </div>
      </div>

      <!-- dynamic job content (hidden until logged in) -->
      <div id="workContent" style="display:none;flex-direction:column;gap:14px"></div>
    </div>
```

- [ ] **Step 3: Add 5th nav button**

In the `<nav>` block, after the Profile nav button, add:

```html
    <button class="nav-btn" onclick="showPage('work',this)" data-page="work">
      <span class="icon">💼</span>Work
    </button>
```

- [ ] **Step 4: Add Jobs JS**

In the `<script>` block, before the closing `boot()` call, add:

```js
// ── Work / Jobs ───────────────────────────────────────────────────────────────
let workState = {
  status: null,       // last fetched status object from /api/work/status
  localTaps: 0,       // taps since last sync
  localEarned: 0,     // earnings since last sync
  totalEarned: 0,     // total earned this shift (server earned + pending local)
  totalTaps: 0,       // total taps this shift (server taps + pending local)
  syncing: false,
  cooldownInterval: null,
  jobEmoji: '💼',
};

async function openWorkPage() {
  if (!state.userId) {
    document.getElementById('workLoginWall').style.display = '';
    document.getElementById('workContent').style.display = 'none';
    return;
  }
  document.getElementById('workLoginWall').style.display = 'none';
  document.getElementById('workContent').style.display = 'flex';
  await refreshWorkStatus();
}

function loginFromWork() {
  const v = document.getElementById('workUserId').value.trim();
  if (!v) return;
  setUser(v);
  openWorkPage();
}

async function refreshWorkStatus() {
  if (!state.userId) return;
  try {
    const s = await api(`/api/work/status/${state.userId}`);
    workState.status = s;
    renderWorkPage(s);
  } catch(e) {
    document.getElementById('workContent').innerHTML = `<div class="error-msg">${e.message}</div>`;
  }
}

const JOB_EMOJIS = ['🧑‍🎓','📈','🌾','🔍','⚙️','🦈','👑'];

function renderWorkPage(s) {
  const content = document.getElementById('workContent');
  clearInterval(workState.cooldownInterval);

  const emojiRaw = JOB_EMOJIS[s.job.tier_index] || '💼';
  workState.jobEmoji = emojiRaw;

  // Build tier bar
  const nextJob = s.next_job;
  const tierPct = nextJob
    ? Math.min(100, Math.round((1 - nextJob.taps_remaining / nextJob.taps_required) * 100))
    : 100;
  const nextLabel = nextJob
    ? `${nextJob.taps_remaining} taps to ${nextJob.title}`
    : '👑 Max tier!';

  const jobHero = `
    <div class="job-hero">
      <div style="font-size:48px">${emojiRaw}</div>
      <div class="job-title">${esc(s.job.title)}</div>
      <div class="job-earn-range">${fmt(s.job.earn_low)}–${fmt(s.job.earn_high)} WRK$ per tap</div>
      <div class="job-next">${esc(nextLabel)}</div>
      <div class="tier-bar-wrap" style="margin-top:4px">
        <div class="tier-bar-fill" style="width:${tierPct}%"></div>
      </div>
    </div>`;

  // ── State: active shift ──
  if (s.session) {
    workState.totalTaps = s.session.taps + workState.localTaps;
    workState.totalEarned = s.session.earned + workState.localEarned;
    const pct = Math.min(100, Math.round(workState.totalTaps / 50 * 100));
    content.innerHTML = `
      ${jobHero}
      <div class="card">
        <div class="shift-header">
          <div>
            <div style="font-size:11px;color:var(--muted);margin-bottom:2px">Earned this shift</div>
            <div class="shift-earned" id="shiftEarned">${fmt(workState.totalEarned)} WRK$</div>
          </div>
          <div class="shift-taps" id="shiftTaps">${workState.totalTaps} / 50 taps</div>
        </div>
        <div class="shift-progress-wrap">
          <div class="shift-progress-fill" id="shiftProgress" style="width:${pct}%"></div>
        </div>
        <div class="tap-zone-wrap">
          <div class="tap-zone" id="tapZone" onclick="handleTap(event)">${emojiRaw}</div>
        </div>
        <button class="btn outline" style="width:100%" onclick="collectShift()">🏁 Collect Early</button>
      </div>`;
    return;
  }

  // ── State: cooldown ──
  if (s.cooldown_remaining > 0) {
    content.innerHTML = `
      ${jobHero}
      <div class="card cooldown-card">
        <div class="cooldown-icon">⏳</div>
        <div class="cooldown-label">Next shift available in</div>
        <div class="cooldown-timer" id="cdTimer">${fmtCountdown(s.cooldown_remaining)}</div>
      </div>`;
    let remaining = s.cooldown_remaining;
    workState.cooldownInterval = setInterval(() => {
      remaining--;
      const el = document.getElementById('cdTimer');
      if (!el) { clearInterval(workState.cooldownInterval); return; }
      if (remaining <= 0) {
        clearInterval(workState.cooldownInterval);
        refreshWorkStatus();
        return;
      }
      el.textContent = fmtCountdown(remaining);
    }, 1000);
    return;
  }

  // ── State: ready ──
  content.innerHTML = `
    ${jobHero}
    <button class="btn" style="width:100%;padding:16px;font-size:16px" onclick="startShift()">
      🟢 Start Shift
    </button>
    <div style="text-align:center;font-size:12px;color:var(--muted)">
      Earn up to ${fmt(s.job.earn_high * 50)} WRK$ per shift · 15 min cooldown
    </div>`;
}

function fmtCountdown(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

async function startShift() {
  workState.localTaps = 0;
  workState.localEarned = 0;
  workState.totalTaps = 0;
  workState.totalEarned = 0;
  try {
    const s = await api('/api/work/start', { method:'POST', json:{ user_id:+state.userId } });
    workState.status = s;
    renderWorkPage(s);
  } catch(e) {
    alert(e.message);
  }
}

function handleTap(evt) {
  const s = workState.status;
  if (!s || !s.session) return;
  if (workState.totalTaps >= 50) return;

  const lo = s.job.earn_low;
  const hi = s.job.earn_high;
  const earned = Math.floor(Math.random() * (hi - lo + 1)) + lo;

  workState.localTaps++;
  workState.localEarned += earned;
  workState.totalTaps++;
  workState.totalEarned += earned;

  // update UI instantly
  const earnEl = document.getElementById('shiftEarned');
  const tapsEl = document.getElementById('shiftTaps');
  const progEl = document.getElementById('shiftProgress');
  if (earnEl) earnEl.textContent = `${fmt(workState.totalEarned)} WRK$`;
  if (tapsEl) tapsEl.textContent = `${workState.totalTaps} / 50 taps`;
  if (progEl) progEl.style.width = `${Math.min(100, Math.round(workState.totalTaps / 50 * 100))}%`;

  // floating +X text
  spawnFloatText(`+${fmt(earned)}`, evt.clientX, evt.clientY);

  // batch sync every 5 taps
  if (workState.localTaps % 5 === 0) {
    syncBatch();
  }
}

function spawnFloatText(text, x, y) {
  const el = document.createElement('div');
  el.className = 'float-earn';
  el.textContent = text;
  el.style.left = `${x - 20}px`;
  el.style.top = `${y - 20}px`;
  document.body.appendChild(el);
  el.addEventListener('animationend', () => el.remove());
}

async function syncBatch() {
  if (workState.syncing || workState.localTaps === 0) return;
  workState.syncing = true;
  const batchTaps = workState.localTaps;
  const batchEarned = workState.localEarned;
  workState.localTaps = 0;
  workState.localEarned = 0;
  try {
    const result = await api('/api/work/sync', {
      method: 'POST',
      json: { user_id: +state.userId, taps_delta: batchTaps, earned_delta: batchEarned },
    });
    if (result.auto_ended) {
      handleShiftComplete(result);
      return;
    }
    // reconcile: use server totals as source of truth
    workState.status.session = result.session;
    workState.totalTaps = result.session.taps + workState.localTaps;
    workState.totalEarned = result.session.earned + workState.localEarned;
  } catch(e) {
    // sync failed — put taps back in pending queue, retry next batch
    workState.localTaps += batchTaps;
    workState.localEarned += batchEarned;
  }
  workState.syncing = false;
}

async function collectShift() {
  // flush any pending local taps first
  if (workState.localTaps > 0) {
    await syncBatch();
  }
  try {
    const result = await api('/api/work/end', { method:'POST', json:{ user_id:+state.userId } });
    handleShiftComplete(result);
  } catch(e) {
    alert(e.message);
  }
}

function handleShiftComplete(result) {
  workState.localTaps = 0;
  workState.localEarned = 0;
  workState.syncing = false;
  state.balance = result.new_balance;
  refreshHeaderBal();

  const content = document.getElementById('workContent');
  const promoLine = result.promoted ? `<div style="color:var(--primary);font-weight:700;margin-top:8px">🎉 Promoted to ${esc(result.new_job)}!</div>` : '';
  content.innerHTML = `
    <div class="card" style="text-align:center;padding:28px 20px;display:flex;flex-direction:column;align-items:center;gap:10px">
      <div style="font-size:48px">🏁</div>
      <div style="font-size:22px;font-weight:800">Shift Complete!</div>
      <div style="font-size:28px;font-weight:800;color:var(--gold)">+${fmt(result.collected)} WRK$</div>
      <div style="font-size:13px;color:var(--muted)">${result.taps} taps · Balance: ${fmt(result.new_balance)} WRK$</div>
      ${promoLine}
    </div>`;

  // refresh full status after 2s (will show cooldown state)
  setTimeout(() => refreshWorkStatus(), 2000);
}
```

- [ ] **Step 5: Wire up the nav click to call openWorkPage**

In the existing `showPage` function, the nav click already fires. But `openWorkPage` needs to trigger on every visit to the Work page. Replace the existing `showPage` function:

```js
function showPage(id, btn) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('page-' + id).classList.add('active');
  btn.classList.add('active');
  if (id === 'lb') loadLeaderboard(state.lbTab);
  if (id === 'work') openWorkPage();
}
```

- [ ] **Step 6: Also call openWorkPage in boot() if user is logged in**

In the `boot()` function, after `refreshBalance()`, add nothing — the page isn't active on boot. The nav click handles it. No change needed here.

- [ ] **Step 7: Verify the HTML is valid**

```bash
python -c "
from pathlib import Path
html = Path('miniapp/static/index.html').read_text()
assert 'page-work' in html
assert 'tap-zone' in html
assert 'handleTap' in html
assert 'syncBatch' in html
assert 'openWorkPage' in html
print('OK')
"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add miniapp/static/index.html
git commit -m "feat: Jobs page UI with tap game and batch sync"
```

---

## Task 5: Push and deploy

- [ ] **Step 1: Run full test suite**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -m pytest tests/test_work_sessions_db.py tests/test_economy_db.py tests/test_jobs.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Push to GitHub**

```bash
git push
```

- [ ] **Step 3: Deploy to Pi**

On the Pi:
```bash
git pull && systemctl --user restart wrkshelperbot
```

- [ ] **Step 4: Smoke test on Pi**

In Telegram DMs with the bot:
1. `/work` — should start a shift, show job title and tap buttons
2. Tap ⚡ Work a few times — each tap shows `+X WRK$` toast
3. Tap 🏁 End Shift — should collect and show summary
4. `/work` again immediately — should show cooldown timer

Then in the mini-app:
1. Open Jobs page, enter user ID
2. Should show current job tier and "Start Shift" button (if cooldown expired) or countdown
3. Start shift, tap zone, verify floating text and progress bar
4. Let it auto-end or hit Collect — verify balance updates in header pill
