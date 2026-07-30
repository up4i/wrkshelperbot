# Mini-App Games Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Roulette, High-Low, Street Craps, Hack (mini-app), and Rob (mini-app) to the games grid at `miniapp.wrk.money`, migrating Hack and Rob from pure in-memory bot state to a shared SQLite backend.

**Architecture:** All game endpoints live in `miniapp/server.py` (sync SQLite via `db_conn()`). Session tables (`craps_sessions`, `highlow_sessions`, `hack_sessions`) are created in both `_startup()` (mini-app) and `db.py` `_migrate()` (bot) so both processes are self-sufficient. Pure game logic goes into `handlers/economy.py` as testable functions; server.py duplicates only the minimal math it needs inline (matching the existing `_slot_payout` / `_slots_result` duplication pattern).

**Tech Stack:** Python + FastAPI + SQLite (server); vanilla JS + HTML (frontend); aiosqlite (bot side DB); `urllib.request` for Telegram DMs (existing pattern in server.py).

---

## File Map

| File | Change |
|---|---|
| `db.py` | `_migrate()`: add `last_rob`/`last_hack` economy columns + `hack_sessions` table; new `get/set_rob_cooldown`, `get/set_hack_cooldown`, `get/save/update/delete_hack_session` async fns |
| `handlers/economy.py` | Add pure fns `_roulette_result`, `_craps_come_out`, `_highlow_result`; rewrite `cmd_rob` to use DB cooldown; rewrite `cmd_hack`/`cmd_guess` to use `hack_sessions` table |
| `miniapp/server.py` | `_startup()`: add 3 session tables + 2 economy columns; add 11 new endpoints + 6 Pydantic models |
| `miniapp/static/index.html` | Add 5 game cards to `gamesGrid`; add 5 modals (HTML); add JS functions for each game |
| `tests/test_economy_logic.py` | Add tests for `_roulette_result`, `_craps_come_out`, `_highlow_result` |

---

## Task 1: DB Migrations

**Files:**
- Modify: `db.py:124-166` (`_migrate` function)
- Modify: `miniapp/server.py:1209-1231` (`_startup` function)

- [ ] **Step 1: Add migrations to `db.py` `_migrate()`**

In `db.py`, inside `_migrate(db)` after the `econ_new` block (after line 138 "await db.commit()"), add:

```python
    # last_rob / last_hack cooldown columns
    for col in ("last_rob INTEGER NOT NULL DEFAULT 0", "last_hack INTEGER NOT NULL DEFAULT 0"):
        col_name = col.split()[0]
        if col_name not in econ_cols:
            await db.execute(f"ALTER TABLE economy ADD COLUMN {col}")
            await db.commit()

    # hack_sessions table (shared with mini-app)
    await db.execute("""CREATE TABLE IF NOT EXISTS hack_sessions (
        user_id          INTEGER PRIMARY KEY,
        word             TEXT    NOT NULL,
        clue             TEXT    NOT NULL,
        reward           INTEGER NOT NULL,
        attempts         INTEGER NOT NULL DEFAULT 5,
        revealed_indices TEXT    NOT NULL DEFAULT '0',
        started_at       INTEGER NOT NULL
    )""")
    await db.commit()
```

- [ ] **Step 2: Add migrations to `miniapp/server.py` `_startup()`**

In `miniapp/server.py`, inside `_startup()` after the existing `db.commit()` (after line 1231), add:

```python
        for col in ("last_rob INTEGER NOT NULL DEFAULT 0", "last_hack INTEGER NOT NULL DEFAULT 0"):
            try:
                db.execute(f"ALTER TABLE economy ADD COLUMN {col}")
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
        db.execute("""CREATE TABLE IF NOT EXISTS highlow_sessions (
            user_id      INTEGER PRIMARY KEY,
            bet          INTEGER NOT NULL,
            current_card INTEGER NOT NULL,
            multiplier   REAL    NOT NULL DEFAULT 1.0,
            started_at   INTEGER NOT NULL
        )""")
        db.commit()
```

- [ ] **Step 3: Add DB helper functions to `db.py`**

Append these functions at the end of `db.py` (after `mark_reminder_sent`):

```python
async def get_rob_cooldown(db_path: str, user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT last_rob FROM economy WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def set_rob_cooldown(db_path: str, user_id: int, timestamp: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE economy SET last_rob = ? WHERE user_id = ?", (timestamp, user_id)
        )
        await db.commit()


async def get_hack_cooldown(db_path: str, user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT last_hack FROM economy WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def set_hack_cooldown(db_path: str, user_id: int, timestamp: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE economy SET last_hack = ? WHERE user_id = ?", (timestamp, user_id)
        )
        await db.commit()


async def get_hack_session(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM hack_sessions WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_hack_session(
    db_path: str, user_id: int, word: str, clue: str,
    reward: int, revealed_indices: str
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR REPLACE INTO hack_sessions
               (user_id, word, clue, reward, attempts, revealed_indices, started_at)
               VALUES (?, ?, ?, ?, 5, ?, ?)""",
            (user_id, word, clue, reward, revealed_indices, now),
        )
        await db.commit()


async def update_hack_session(
    db_path: str, user_id: int, attempts: int, revealed_indices: str
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE hack_sessions SET attempts = ?, revealed_indices = ? WHERE user_id = ?",
            (attempts, revealed_indices, user_id),
        )
        await db.commit()


async def delete_hack_session(db_path: str, user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM hack_sessions WHERE user_id = ?", (user_id,))
        await db.commit()
```

- [ ] **Step 4: Run tests to confirm no regressions**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add db.py miniapp/server.py
git commit -m "feat: DB migrations + helpers for rob/hack/craps/highlow sessions"
```

---

## Task 2: Bot Handler Updates (Rob + Hack → DB)

**Files:**
- Modify: `handlers/economy.py:860` (`cmd_rob` and `_rob_cooldowns`)
- Modify: `handlers/economy.py:737-856` (`cmd_hack`, `cmd_guess`, and in-memory dicts)

Context: `_rob_cooldowns` is a dict on line 860. `_hack_cooldowns` is on line 737. `_hack_games` is on line 738.

- [ ] **Step 1: Add pure game logic functions to `economy.py`**

After the `_rob_getaway` flavor text block and before `cmd_rob` (around line 903), add these pure functions (needed for tests in Task 3 and used inline in economy.py/server.py):

```python
def _roulette_result(slot: int, color: str) -> tuple[bool, int]:
    """slot is 0-37: 0-1=green (2/38), 2-19=red (18/38), 20-37=black (18/38). Returns (won, payout_mult)."""
    if slot <= 1:
        won = color == "green"
        return won, 14 if won else 0
    elif slot <= 19:
        won = color == "red"
        return won, 2 if won else 0
    else:
        won = color == "black"
        return won, 2 if won else 0


def _craps_come_out(total: int) -> str:
    """Returns 'win', 'lose', or 'point' for a come-out dice roll total."""
    if total in (7, 11):
        return "win"
    if total in (2, 3, 12):
        return "lose"
    return "point"


def _highlow_result(current: int, next_card: int, direction: str) -> str:
    """Returns 'correct' or 'wrong'. Equal rank counts as wrong."""
    if direction == "higher":
        return "correct" if next_card > current else "wrong"
    return "correct" if next_card < current else "wrong"
```

- [ ] **Step 2: Update `cmd_rob` to use DB cooldown**

Replace the `_rob_cooldowns` dict (line 860) and its usage in `cmd_rob`:

Remove:
```python
_rob_cooldowns: dict[int, float] = {}  # user_id -> timestamp
```

In `cmd_rob` (around line 910-913), replace:
```python
    now = time.time()
    last_rob = _rob_cooldowns.get(robber.id, 0)
    if now - last_rob < 900:
```
With:
```python
    now = time.time()
    last_rob = await db.get_rob_cooldown(config.DB_PATH, robber.id)
    if now - last_rob < 900:
```

Replace (around line 940):
```python
    _rob_cooldowns[robber.id] = now
```
With:
```python
    await db.set_rob_cooldown(config.DB_PATH, robber.id, int(now))
```

- [ ] **Step 3: Update `cmd_hack` and `cmd_guess` to use DB**

Remove the in-memory state declarations:
```python
_hack_cooldowns: dict[int, float] = {}   # user_id -> timestamp
_hack_games: dict[int, dict] = {}        # user_id -> active game state
_HACK_GAME_TTL = 3600  # abandon after 1 hour
```

Replace `cmd_hack` body entirely:

```python
@topic_gated
async def cmd_hack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    await _ensure_wallet(user, config.DB_PATH)

    now = time.time()
    last = await db.get_hack_cooldown(config.DB_PATH, user.id)
    if now - last < 3600:
        remaining = int(3600 - (now - last))
        m, s = divmod(remaining, 60)
        await msg.reply_text(f"⏳ Hack cooldown: {m}m {s}s remaining.")
        return

    existing = await db.get_hack_session(config.DB_PATH, user.id)
    if existing:
        if now - existing["started_at"] > 3600:
            await db.delete_hack_session(config.DB_PATH, user.id)
            await db.set_hack_cooldown(config.DB_PATH, user.id, int(now))
        else:
            revealed = set(int(x) for x in existing["revealed_indices"].split(",") if x)
            display = _hack_display(existing["word"], revealed)
            await msg.reply_text(
                f"🖥️ You already have an active hack session!\n\n"
                f"`{display}`\n_{existing['clue']}_\n\n"
                f"Attempts left: {existing['attempts']}\nUse `/guess <word>` to answer.",
                parse_mode="Markdown"
            )
        return

    word, clue = random.choice(_WORDLIST)
    reward = random.randint(5000, 15000)
    revealed_indices = "0"  # always reveal first letter

    await db.save_hack_session(config.DB_PATH, user.id, word, clue, reward, revealed_indices)

    display = _hack_display(word, {0})
    await msg.reply_text(
        f"🖥️ *Hacking a wallet...*\n\n"
        f"Clue: _{clue}_\n\n"
        f"`{display}` ({len(word)} letters)\n\n"
        f"You have 5 attempts. Use `/guess <word>` to crack it.\n"
        f"💰 Reward: {reward:,} WRK$",
        parse_mode="Markdown"
    )
```

Replace `cmd_guess` body entirely:

```python
@topic_gated
async def cmd_guess(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    game = await db.get_hack_session(config.DB_PATH, user.id)
    if not game:
        await msg.reply_text("❌ No active hack session. Start one with `/hack`.", parse_mode="Markdown")
        return

    if not ctx.args:
        await msg.reply_text("Usage: `/guess <word>`", parse_mode="Markdown")
        return

    guess = " ".join(ctx.args).lower().strip()
    word = game["word"]
    revealed = set(int(x) for x in game["revealed_indices"].split(",") if x)

    if guess == word:
        await db.delete_hack_session(config.DB_PATH, user.id)
        await db.set_hack_cooldown(config.DB_PATH, user.id, int(time.time()))
        reward = game["reward"]
        new_bal = await db.update_balance(config.DB_PATH, user.id, reward)
        await msg.reply_text(
            f"✅ *ACCESS GRANTED*\n\n"
            f"The word was `{word}`.\n"
            f"You cracked the seed phrase and drained the wallet!\n\n"
            f"💰 +{reward:,} WRK$ earned\n"
            f"Balance: {new_bal:,} WRK$",
            parse_mode="Markdown"
        )
        return

    attempts_left = game["attempts"] - 1

    if attempts_left <= 0:
        await db.delete_hack_session(config.DB_PATH, user.id)
        await db.set_hack_cooldown(config.DB_PATH, user.id, int(time.time()))
        await msg.reply_text(
            f"❌ *CONNECTION TERMINATED*\n\n"
            f"The word was `{word}`.\n"
            f"You got traced. Better luck next time.",
            parse_mode="Markdown"
        )
        return

    unrevealed = [i for i in range(len(word)) if i not in revealed]
    if unrevealed:
        revealed.add(random.choice(unrevealed))

    new_revealed_str = ",".join(str(i) for i in sorted(revealed))
    await db.update_hack_session(config.DB_PATH, user.id, attempts_left, new_revealed_str)

    display = _hack_display(word, revealed)
    await msg.reply_text(
        f"❌ Wrong. {attempts_left} attempt(s) left.\n\n"
        f"`{display}`\n_{game['clue']}_",
        parse_mode="Markdown"
    )
```

- [ ] **Step 4: Run tests**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add handlers/economy.py
git commit -m "feat: migrate rob/hack from in-memory dicts to DB; add pure game logic fns"
```

---

## Task 3: Tests for Pure Game Logic Functions

**Files:**
- Modify: `tests/test_economy_logic.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_economy_logic.py`:

```python
from handlers.economy import _roulette_result, _craps_come_out, _highlow_result


# ── Roulette ──────────────────────────────────────────────────────────────────

def test_roulette_green_slot_wins_on_green():
    won, mult = _roulette_result(0, "green")
    assert won is True
    assert mult == 14

def test_roulette_green_slot_loses_on_red():
    won, mult = _roulette_result(1, "red")
    assert won is False
    assert mult == 0

def test_roulette_red_slot_wins_on_red():
    won, mult = _roulette_result(2, "red")
    assert won is True
    assert mult == 2

def test_roulette_red_slot_loses_on_black():
    won, mult = _roulette_result(10, "black")
    assert won is False
    assert mult == 0

def test_roulette_black_slot_wins_on_black():
    won, mult = _roulette_result(20, "black")
    assert won is True
    assert mult == 2

def test_roulette_black_boundary_slot_37():
    won, mult = _roulette_result(37, "black")
    assert won is True
    assert mult == 2

def test_roulette_red_boundary_slot_19():
    won, mult = _roulette_result(19, "red")
    assert won is True
    assert mult == 2


# ── Craps ─────────────────────────────────────────────────────────────────────

def test_craps_come_out_7_wins():
    assert _craps_come_out(7) == "win"

def test_craps_come_out_11_wins():
    assert _craps_come_out(11) == "win"

def test_craps_come_out_2_loses():
    assert _craps_come_out(2) == "lose"

def test_craps_come_out_3_loses():
    assert _craps_come_out(3) == "lose"

def test_craps_come_out_12_loses():
    assert _craps_come_out(12) == "lose"

def test_craps_come_out_4_sets_point():
    assert _craps_come_out(4) == "point"

def test_craps_come_out_10_sets_point():
    assert _craps_come_out(10) == "point"


# ── High-Low ──────────────────────────────────────────────────────────────────

def test_highlow_higher_correct():
    assert _highlow_result(5, 8, "higher") == "correct"

def test_highlow_higher_wrong():
    assert _highlow_result(8, 5, "higher") == "wrong"

def test_highlow_higher_equal_is_wrong():
    assert _highlow_result(7, 7, "higher") == "wrong"

def test_highlow_lower_correct():
    assert _highlow_result(9, 3, "lower") == "correct"

def test_highlow_lower_wrong():
    assert _highlow_result(3, 9, "lower") == "wrong"

def test_highlow_lower_equal_is_wrong():
    assert _highlow_result(5, 5, "lower") == "wrong"
```

- [ ] **Step 2: Run tests — expect all to pass**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/test_economy_logic.py -v
```
Expected: new tests pass alongside existing ones.

- [ ] **Step 3: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add tests/test_economy_logic.py
git commit -m "test: add roulette, craps come-out, highlow pure logic tests"
```

---

## Task 4: Roulette Endpoint

**Files:**
- Modify: `miniapp/server.py` (add after `play_coinflip` endpoint)

- [ ] **Step 1: Add Pydantic model and endpoint**

After the `play_coinflip` function (around line 665), add:

```python
class RouletteRequest(BaseModel):
    user_id: int
    bet: int
    color: str


@app.post("/api/play/roulette")
def play_roulette(req: RouletteRequest):
    if req.color not in ("red", "black", "green"):
        raise HTTPException(400, "color must be red, black, or green")
    with db_conn() as db:
        bal = _deduct_and_check(db, req.user_id, req.bet)
        slot = random.randint(0, 37)
        if slot <= 1:
            winning_color = "green"
            payout_mult = 14
        elif slot <= 19:
            winning_color = "red"
            payout_mult = 2
        else:
            winning_color = "black"
            payout_mult = 2
        won = req.color == winning_color
        delta = req.bet * (payout_mult - 1) if won else -req.bet
        new_bal = bal + delta
        db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
        db.commit()
        return {
            "slot": slot,
            "winning_color": winning_color,
            "won": won,
            "payout_mult": payout_mult if won else 0,
            "delta": delta,
            "new_balance": new_bal,
        }
```

- [ ] **Step 2: Run tests**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/server.py
git commit -m "feat: add /api/play/roulette endpoint"
```

---

## Task 5: High-Low Endpoints

**Files:**
- Modify: `miniapp/server.py`

- [ ] **Step 1: Add Pydantic models and endpoints**

After the roulette endpoint, add:

```python
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
        if req.direction == "higher":
            correct = next_card > sess["current_card"]
        else:
            correct = next_card < sess["current_card"]

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
            return {
                "result": "wrong",
                "next_card": next_card,
                "next_card_label": _card_label(next_card),
                "lost": sess["bet"],
                "new_balance": db.execute(
                    "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
                ).fetchone()["balance"],
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
        return {
            "winnings": winnings,
            "multiplier": sess["multiplier"],
            "new_balance": new_bal,
        }


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
```

- [ ] **Step 2: Run tests**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/server.py
git commit -m "feat: add /api/play/highlow/start|guess|cashout|status endpoints"
```

---

## Task 6: Street Craps Endpoints

**Files:**
- Modify: `miniapp/server.py`

- [ ] **Step 1: Add Pydantic models and endpoints**

After the highlow endpoints, add:

```python
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
        d1 = random.randint(1, 6)
        d2 = random.randint(1, 6)
        total = d1 + d2

        if sess["point"] is None:
            # Come-out roll
            if total in (7, 11):
                winnings = sess["bet"] * 2
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                new_bal = row["balance"] + winnings
                db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "win",
                        "winnings": winnings, "new_balance": new_bal}
            elif total in (2, 3, 12):
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "lose",
                        "lost": sess["bet"], "new_balance": row["balance"]}
            else:
                db.execute(
                    "UPDATE craps_sessions SET point = ? WHERE user_id = ?", (total, req.user_id)
                )
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "point",
                        "point": total}
        else:
            # Point phase
            if total == sess["point"]:
                winnings = sess["bet"] * 2
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                new_bal = row["balance"] + winnings
                db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "win",
                        "winnings": winnings, "new_balance": new_bal}
            elif total == 7:
                db.execute("DELETE FROM craps_sessions WHERE user_id = ?", (req.user_id,))
                row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
                db.commit()
                return {"d1": d1, "d2": d2, "total": total, "result": "lose",
                        "lost": sess["bet"], "new_balance": row["balance"]}
            else:
                return {"d1": d1, "d2": d2, "total": total, "result": "rolling",
                        "point": sess["point"]}


@app.get("/api/play/craps/status/{user_id}")
def craps_status(user_id: int):
    with db_conn() as db:
        sess = db.execute(
            "SELECT * FROM craps_sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not sess:
            return {"active": False}
        return {"active": True, **dict(sess)}
```

- [ ] **Step 2: Run tests**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/server.py
git commit -m "feat: add /api/play/craps/start|roll|status endpoints"
```

---

## Task 7: Hack Mini-App Endpoints

**Files:**
- Modify: `miniapp/server.py`

Context: `_WORDLIST` and `_hack_display` live in `handlers/economy.py`. Since server.py already does `sys.path.insert(0, parent)`, add the import at the top of server.py.

- [ ] **Step 1: Add imports + endpoint**

At the top of `miniapp/server.py`, after `import config`, add:

```python
from handlers.economy import _WORDLIST, _hack_display
```

Then add the hack endpoints after the craps endpoints:

```python
class HackGuessRequest(BaseModel):
    user_id: int
    word: str


class HackStartRequest(BaseModel):
    user_id: int


_HACK_COOLDOWN = 3600


@app.get("/api/hack/status/{user_id}")
def hack_status(user_id: int):
    with db_conn() as db:
        row = db.execute(
            "SELECT last_hack FROM economy WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        now = int(time.time())
        cooldown_remaining = max(0, _HACK_COOLDOWN - (now - (row["last_hack"] or 0)))
        sess = db.execute(
            "SELECT * FROM hack_sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
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
        row = db.execute(
            "SELECT last_hack FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found — use the bot first")
        now = int(time.time())
        cooldown_remaining = max(0, _HACK_COOLDOWN - (now - (row["last_hack"] or 0)))
        if cooldown_remaining > 0:
            raise HTTPException(400, f"Hack on cooldown for {cooldown_remaining}s")
        existing = db.execute(
            "SELECT user_id FROM hack_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if existing:
            raise HTTPException(400, "You already have an active hack session")
        word, clue = random.choice(_WORDLIST)
        reward = random.randint(5000, 15000)
        db.execute(
            """INSERT INTO hack_sessions (user_id, word, clue, reward, attempts, revealed_indices, started_at)
               VALUES (?, ?, ?, ?, 5, '0', ?)""",
            (req.user_id, word, clue, reward, now),
        )
        db.commit()
        display = _hack_display(word, {0})
        return {
            "display": display,
            "clue": clue,
            "attempts": 5,
            "reward": reward,
            "word_length": len(word),
        }


@app.post("/api/hack/guess")
def hack_guess(req: HackGuessRequest):
    guess = req.word.lower().strip()
    with db_conn() as db:
        sess = db.execute(
            "SELECT * FROM hack_sessions WHERE user_id = ?", (req.user_id,)
        ).fetchone()
        if not sess:
            raise HTTPException(404, "No active hack session")
        sess = dict(sess)
        word = sess["word"]
        revealed = set(int(x) for x in sess["revealed_indices"].split(",") if x)

        if guess == word:
            db.execute("DELETE FROM hack_sessions WHERE user_id = ?", (req.user_id,))
            db.execute(
                "UPDATE economy SET last_hack = ? WHERE user_id = ?", (int(time.time()), req.user_id)
            )
            row = db.execute("SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)).fetchone()
            new_bal = row["balance"] + sess["reward"]
            db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_bal, req.user_id))
            db.commit()
            return {"result": "win", "word": word, "reward": sess["reward"], "new_balance": new_bal}

        attempts_left = sess["attempts"] - 1
        if attempts_left <= 0:
            db.execute("DELETE FROM hack_sessions WHERE user_id = ?", (req.user_id,))
            db.execute(
                "UPDATE economy SET last_hack = ? WHERE user_id = ?", (int(time.time()), req.user_id)
            )
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
        return {
            "result": "wrong",
            "display": display,
            "attempts_left": attempts_left,
        }
```

- [ ] **Step 2: Run tests**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/server.py
git commit -m "feat: add /api/hack/status|start|guess endpoints"
```

---

## Task 8: Rob Mini-App Endpoints

**Files:**
- Modify: `miniapp/server.py`

Context: flavor text arrays (`_ROB_SUCCESS`, `_ROB_FINE`, `_ROB_BAIL`, `_ROB_GETAWAY`) and `_rob_outcome` are in `handlers/economy.py`. Import them.

- [ ] **Step 1: Extend the import from handlers.economy**

Update the import added in Task 7:

```python
from handlers.economy import (
    _WORDLIST, _hack_display,
    _rob_outcome, _ROB_SUCCESS, _ROB_FINE, _ROB_BAIL, _ROB_GETAWAY,
)
```

- [ ] **Step 2: Add Rob endpoints after hack endpoints**

```python
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
        pass  # DM failures are non-fatal


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
            "SELECT balance, last_rob FROM economy WHERE user_id = ?", (req.user_id,)
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

        db.execute(
            "UPDATE economy SET last_rob = ? WHERE user_id = ?", (now, req.user_id)
        )

        success = random.random() < 0.50
        result = _rob_outcome(success, robber_row["balance"], target_row["balance"])
        robber_name = "You"
        target_name = target_row["name"]

        if result["outcome"] == "success":
            amount = result["amount"]
            db.execute(
                "UPDATE economy SET balance = balance - ? WHERE user_id = ?",
                (amount, req.target_id),
            )
            db.execute(
                "UPDATE economy SET balance = balance + ? WHERE user_id = ?",
                (amount, req.user_id),
            )
            emoji, template = random.choice(_ROB_SUCCESS)
            flavor = template.format(robber=robber_name, target=target_name, amount=f"{amount:,}")
            victim_msg = f"{emoji} Your wallet was robbed! {robber_name} stole {amount:,} WRK$ from you."
            _send_telegram_dm(req.target_id, victim_msg)
        elif result["outcome"] == "fine":
            amount = result["amount"]
            db.execute(
                "UPDATE economy SET balance = MAX(0, balance - ?) WHERE user_id = ?",
                (amount, req.user_id),
            )
            emoji, template = random.choice(_ROB_FINE)
            flavor = template.format(robber=robber_name, target=target_name, amount=f"{amount:,}")
        elif result["outcome"] == "bail":
            amount = result["amount"]
            db.execute(
                "UPDATE economy SET balance = MAX(0, balance - ?) WHERE user_id = ?",
                (amount, req.user_id),
            )
            emoji, template = random.choice(_ROB_BAIL)
            flavor = template.format(robber=robber_name, target=target_name, amount=f"{amount:,}")
        else:
            amount = 0
            emoji, template = random.choice(_ROB_GETAWAY)
            flavor = template.format(robber=robber_name, target=target_name, amount="0")

        new_bal = db.execute(
            "SELECT balance FROM economy WHERE user_id = ?", (req.user_id,)
        ).fetchone()["balance"]
        db.commit()

        return {
            "outcome": result["outcome"],
            "emoji": emoji,
            "flavor": flavor,
            "amount": amount,
            "new_balance": new_bal,
        }


@app.get("/api/rob/cooldown/{user_id}")
def rob_cooldown(user_id: int):
    with db_conn() as db:
        row = db.execute("SELECT last_rob FROM economy WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        now = int(time.time())
        remaining = max(0, _ROB_COOLDOWN - (now - (row["last_rob"] or 0)))
        return {"cooldown_remaining": remaining}
```

- [ ] **Step 3: Run tests**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/server.py
git commit -m "feat: add /api/rob/targets|attempt|cooldown endpoints"
```

---

## Task 9: Frontend — Game Cards

**Files:**
- Modify: `miniapp/static/index.html:1010-1031` (games grid)

- [ ] **Step 1: Add 5 game cards to `gamesGrid`**

In `index.html`, replace the closing `</div>` of `gamesGrid` (after the Crash card, around line 1031) by inserting 5 new cards before it:

Find:
```html
        <div class="game-card" onclick="openCrash()">
          <div class="gicon">🚀</div>
          <div class="gname">Crash</div>
          <div class="gdesc">Cash out before it crashes</div>
        </div>
      </div>
```

Replace with:
```html
        <div class="game-card" onclick="openCrash()">
          <div class="gicon">🚀</div>
          <div class="gname">Crash</div>
          <div class="gdesc">Cash out before it crashes</div>
        </div>
        <div class="game-card" onclick="openRoulette()">
          <div class="gicon">🎡</div>
          <div class="gname">Roulette</div>
          <div class="gdesc">Red, Black, or Green — up to 14×</div>
        </div>
        <div class="game-card" onclick="openHighLow()">
          <div class="gicon">🃏</div>
          <div class="gname">High-Low</div>
          <div class="gdesc">Chain multipliers by reading the cards</div>
        </div>
        <div class="game-card" onclick="openCraps()">
          <div class="gicon">🎲</div>
          <div class="gname">Street Craps</div>
          <div class="gdesc">Roll your point, beat the seven</div>
        </div>
        <div class="game-card" onclick="openHack()">
          <div class="gicon">🖥️</div>
          <div class="gname">Hack</div>
          <div class="gdesc">Crack the word, drain the wallet</div>
        </div>
        <div class="game-card" onclick="openRob()">
          <div class="gicon">🔫</div>
          <div class="gname">Rob</div>
          <div class="gdesc">Pick a target and take the bag</div>
        </div>
      </div>
```

- [ ] **Step 2: Verify page renders 9 game cards visually**

Run the server locally and open the games tab in a browser to confirm all 9 cards appear in a grid without layout breaks.

```bash
cd /home/ogkush/Projects/wrkshelperbot && uvicorn miniapp.server:app --port 8420 --reload &
# open http://localhost:8420 → navigate to games tab
```

- [ ] **Step 3: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/static/index.html
git commit -m "feat: add 5 new game cards to games grid (roulette/highlow/craps/hack/rob)"
```

---

## Task 10: Frontend — Roulette Modal + JS

**Files:**
- Modify: `miniapp/static/index.html`

- [ ] **Step 1: Add roulette modal HTML**

Before `<!-- SLOTS MODAL -->` (around line 1170), insert:

```html
<!-- ROULETTE MODAL -->
<div class="modal-bg" id="rouletteModal" onclick="closeModal('rouletteModal',event)">
  <div class="modal" style="position:relative">
    <button class="modal-close" onclick="closeModal('rouletteModal')">✕</button>
    <div class="modal-title">🎡 Roulette</div>
    <div class="result-box" id="rouletteResult"></div>
    <div class="cf-choice" id="rouletteColors">
      <div class="cf-btn" id="rouletteRed" onclick="setRouletteColor('red')" style="background:rgba(239,68,68,.15);border-color:rgba(239,68,68,.5)">🔴 Red  ×2</div>
      <div class="cf-btn" id="rouletteBlack" onclick="setRouletteColor('black')" style="background:rgba(30,30,40,.5);border-color:rgba(100,100,120,.5)">⚫ Black ×2</div>
      <div class="cf-btn" id="rouletteGreen" onclick="setRouletteColor('green')" style="background:rgba(16,185,129,.15);border-color:rgba(16,185,129,.5)">🟢 Green ×14</div>
    </div>
    <div class="card-title" style="margin-top:4px">Bet amount</div>
    <div class="bet-presets" id="roulettePresets"></div>
    <div class="bet-row">
      <input class="input bet-input" id="rouletteBet" type="number" value="100" min="10">
      <button class="btn" id="rouletteSpinBtn" onclick="spinRoulette()">SPIN</button>
    </div>
    <div class="balance-display">Balance: <span class="b" id="rouletteBal">—</span> WRK$</div>
    <div class="error-msg" id="rouletteErr"></div>
  </div>
</div>
```

- [ ] **Step 2: Add roulette JS**

Find `function openSlots()` in the script section and before it add:

```javascript
// ── Roulette ──────────────────────────────────────────────────────────────────
let rouletteColor = null;

function openRoulette() {
  if (!state.userId) return alert('Open in Telegram to play.');
  rouletteColor = null;
  ['rouletteRed','rouletteBlack','rouletteGreen'].forEach(id => document.getElementById(id).classList.remove('selected'));
  document.getElementById('rouletteResult').className = 'result-box';
  document.getElementById('rouletteErr').textContent = '';
  document.getElementById('rouletteBal').textContent = state.balance !== null ? fmt(state.balance) : '…';
  buildPresets('roulettePresets', 'rouletteBet');
  document.getElementById('rouletteModal').classList.add('open');
}

function setRouletteColor(c) {
  rouletteColor = c;
  ['rouletteRed','rouletteBlack','rouletteGreen'].forEach(id => document.getElementById(id).classList.remove('selected'));
  document.getElementById('roulette' + c.charAt(0).toUpperCase() + c.slice(1)).classList.add('selected');
}

async function spinRoulette() {
  if (!rouletteColor) return alert('Pick a color first.');
  const bet = parseInt(document.getElementById('rouletteBet').value);
  const errEl = document.getElementById('rouletteErr');
  errEl.textContent = '';
  document.getElementById('rouletteSpinBtn').disabled = true;
  document.getElementById('rouletteResult').className = 'result-box';
  try {
    const data = await api('/api/play/roulette', {
      method: 'POST',
      json: { user_id: +state.userId, bet, color: rouletteColor }
    });
    const resEl = document.getElementById('rouletteResult');
    const colorEmoji = { red: '🔴', black: '⚫', green: '🟢' }[data.winning_color];
    const delta = data.delta > 0 ? `+${fmt(data.delta)}` : fmt(data.delta);
    resEl.textContent = data.won
      ? `${colorEmoji} ${data.winning_color.toUpperCase()}! You win! ${delta} WRK$`
      : `${colorEmoji} ${data.winning_color.toUpperCase()}. You lose. ${delta} WRK$`;
    resEl.className = `result-box show ${data.won ? 'win-box' : 'lose-box'}`;
    state.balance = data.new_balance;
    document.getElementById('rouletteBal').textContent = fmt(state.balance);
    refreshHeaderBal();
  } catch (e) {
    errEl.textContent = e.message || 'Error';
  } finally {
    document.getElementById('rouletteSpinBtn').disabled = false;
  }
}
```

- [ ] **Step 3: Run tests + check UI**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
# Also open browser: http://localhost:8420 → Games → Roulette card
```
Expected: modal opens, color buttons highlight on click, Spin posts to API, result displays.

- [ ] **Step 4: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/static/index.html
git commit -m "feat: roulette modal + JS in mini-app"
```

---

## Task 11: Frontend — High-Low Modal + JS

**Files:**
- Modify: `miniapp/static/index.html`

- [ ] **Step 1: Add High-Low modal HTML**

After the roulette modal, insert:

```html
<!-- HIGH-LOW MODAL -->
<div class="modal-bg" id="hlModal" onclick="closeModal('hlModal',event)">
  <div class="modal" style="position:relative">
    <button class="modal-close" onclick="closeModal('hlModal')">✕</button>
    <div class="modal-title">🃏 High-Low</div>
    <div id="hlStatus" style="text-align:center;color:var(--muted);font-size:13px;margin-bottom:8px"></div>
    <!-- Pre-game: bet input -->
    <div id="hlBetWrap">
      <div class="card-title">Bet amount</div>
      <div class="bet-presets" id="hlPresets"></div>
      <div class="bet-row">
        <input class="input bet-input" id="hlBet" type="number" value="100" min="10">
        <button class="btn" id="hlStartBtn" onclick="startHighLow()">DEAL</button>
      </div>
    </div>
    <!-- In-game -->
    <div id="hlGameWrap" style="display:none;flex-direction:column;gap:12px;align-items:center">
      <div style="font-size:64px;font-weight:900;letter-spacing:-2px" id="hlCard">—</div>
      <div style="font-size:13px;color:var(--muted)">Current card</div>
      <div style="font-size:18px;font-weight:700;color:var(--gold)">
        Multiplier: <span id="hlMult">1.00</span>× · Potential: <span id="hlPotential">—</span> WRK$
      </div>
      <div class="cf-choice" style="gap:12px">
        <button class="btn" id="hlHigherBtn" onclick="guessHighLow('higher')" style="flex:1;background:var(--green)">▲ Higher</button>
        <button class="btn" id="hlLowerBtn" onclick="guessHighLow('lower')" style="flex:1;background:var(--red)">▼ Lower</button>
      </div>
      <button class="btn outline" id="hlCashoutBtn" onclick="cashoutHighLow()" style="width:100%">💰 Cash Out</button>
    </div>
    <div class="result-box" id="hlResult"></div>
    <div class="balance-display">Balance: <span class="b" id="hlBal">—</span> WRK$</div>
    <div class="error-msg" id="hlErr"></div>
  </div>
</div>
```

- [ ] **Step 2: Add High-Low JS**

Before `function openRoulette()`, add:

```javascript
// ── High-Low ──────────────────────────────────────────────────────────────────
let hlActive = false;

async function openHighLow() {
  if (!state.userId) return alert('Open in Telegram to play.');
  document.getElementById('hlResult').className = 'result-box';
  document.getElementById('hlErr').textContent = '';
  document.getElementById('hlBal').textContent = state.balance !== null ? fmt(state.balance) : '…';
  buildPresets('hlPresets', 'hlBet');
  document.getElementById('hlModal').classList.add('open');
  // Check for existing session
  try {
    const status = await api(`/api/play/highlow/status/${state.userId}`);
    if (status.active) {
      _hlEnterGame(status.card_label, status.multiplier, status.potential_win);
    } else {
      _hlShowBet();
    }
  } catch { _hlShowBet(); }
}

function _hlShowBet() {
  hlActive = false;
  document.getElementById('hlBetWrap').style.display = '';
  document.getElementById('hlGameWrap').style.display = 'none';
  document.getElementById('hlStatus').textContent = '';
  document.getElementById('hlCashoutBtn').disabled = true;
}

function _hlEnterGame(cardLabel, mult, potential) {
  hlActive = true;
  document.getElementById('hlBetWrap').style.display = 'none';
  document.getElementById('hlGameWrap').style.display = 'flex';
  document.getElementById('hlCard').textContent = cardLabel;
  document.getElementById('hlMult').textContent = mult.toFixed(2);
  document.getElementById('hlPotential').textContent = fmt(potential);
  document.getElementById('hlCashoutBtn').disabled = mult <= 1.0;
}

async function startHighLow() {
  const bet = parseInt(document.getElementById('hlBet').value);
  document.getElementById('hlErr').textContent = '';
  document.getElementById('hlStartBtn').disabled = true;
  try {
    const data = await api('/api/play/highlow/start', {
      method: 'POST', json: { user_id: +state.userId, bet }
    });
    state.balance = data.new_balance;
    document.getElementById('hlBal').textContent = fmt(state.balance);
    refreshHeaderBal();
    _hlEnterGame(data.card_label, 1.0, bet);
  } catch (e) {
    document.getElementById('hlErr').textContent = e.message || 'Error';
  } finally {
    document.getElementById('hlStartBtn').disabled = false;
  }
}

async function guessHighLow(direction) {
  document.getElementById('hlHigherBtn').disabled = true;
  document.getElementById('hlLowerBtn').disabled = true;
  document.getElementById('hlErr').textContent = '';
  try {
    const data = await api('/api/play/highlow/guess', {
      method: 'POST', json: { user_id: +state.userId, direction }
    });
    if (data.result === 'correct') {
      _hlEnterGame(data.next_card_label, data.multiplier, data.potential_win);
      document.getElementById('hlStatus').textContent = `✅ Correct! Next card: ${data.next_card_label}`;
    } else {
      state.balance = data.new_balance;
      document.getElementById('hlBal').textContent = fmt(state.balance);
      refreshHeaderBal();
      const resEl = document.getElementById('hlResult');
      resEl.textContent = `❌ Wrong! Card was ${data.next_card_label}. Lost ${fmt(data.lost)} WRK$.`;
      resEl.className = 'result-box show lose-box';
      _hlShowBet();
    }
  } catch (e) {
    document.getElementById('hlErr').textContent = e.message || 'Error';
  } finally {
    document.getElementById('hlHigherBtn').disabled = false;
    document.getElementById('hlLowerBtn').disabled = false;
  }
}

async function cashoutHighLow() {
  document.getElementById('hlCashoutBtn').disabled = true;
  try {
    const data = await api('/api/play/highlow/cashout', {
      method: 'POST', json: { user_id: +state.userId }
    });
    state.balance = data.new_balance;
    document.getElementById('hlBal').textContent = fmt(state.balance);
    refreshHeaderBal();
    const resEl = document.getElementById('hlResult');
    resEl.textContent = `💰 Cashed out at ${data.multiplier.toFixed(2)}×! +${fmt(data.winnings)} WRK$`;
    resEl.className = 'result-box show win-box';
    _hlShowBet();
  } catch (e) {
    document.getElementById('hlErr').textContent = e.message || 'Error';
    document.getElementById('hlCashoutBtn').disabled = false;
  }
}
```

- [ ] **Step 3: Run tests + check UI**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Check browser: open High-Low, deal, make guesses, cash out.

- [ ] **Step 4: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/static/index.html
git commit -m "feat: high-low modal + JS in mini-app"
```

---

## Task 12: Frontend — Street Craps Modal + JS

**Files:**
- Modify: `miniapp/static/index.html`

- [ ] **Step 1: Add Craps modal HTML**

After the high-low modal, insert:

```html
<!-- CRAPS MODAL -->
<div class="modal-bg" id="crapsModal" onclick="closeModal('crapsModal',event)">
  <div class="modal" style="position:relative">
    <button class="modal-close" onclick="closeModal('crapsModal')">✕</button>
    <div class="modal-title">🎲 Street Craps</div>
    <div id="crapsStatus" style="text-align:center;font-size:15px;font-weight:600;margin-bottom:4px"></div>
    <!-- Pre-game -->
    <div id="crapsBetWrap">
      <div class="card-title">Bet amount</div>
      <div class="bet-presets" id="crapsPresets"></div>
      <div class="bet-row">
        <input class="input bet-input" id="crapsBet" type="number" value="100" min="10">
        <button class="btn" id="crapsStartBtn" onclick="startCraps()">ROLL</button>
      </div>
    </div>
    <!-- In-game -->
    <div id="crapsGameWrap" style="display:none;flex-direction:column;gap:16px;align-items:center">
      <div style="display:flex;gap:24px;font-size:52px" id="crapsDice">
        <span id="crapsDie1">⬜</span><span id="crapsDie2">⬜</span>
      </div>
      <div style="font-size:16px;font-weight:600" id="crapsTotal"></div>
      <button class="btn" id="crapsRollBtn" onclick="rollCraps()" style="width:100%">🎲 Roll</button>
    </div>
    <div class="result-box" id="crapsResult"></div>
    <div class="balance-display">Balance: <span class="b" id="crapsBal">—</span> WRK$</div>
    <div class="error-msg" id="crapsErr"></div>
  </div>
</div>
```

- [ ] **Step 2: Add Craps JS**

Before `function openRoulette()`, add:

```javascript
// ── Street Craps ──────────────────────────────────────────────────────────────
const _DICE_EMOJI = ['', '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣'];

async function openCraps() {
  if (!state.userId) return alert('Open in Telegram to play.');
  document.getElementById('crapsResult').className = 'result-box';
  document.getElementById('crapsErr').textContent = '';
  document.getElementById('crapsBal').textContent = state.balance !== null ? fmt(state.balance) : '…';
  buildPresets('crapsPresets', 'crapsBet');
  document.getElementById('crapsModal').classList.add('open');
  try {
    const status = await api(`/api/play/craps/status/${state.userId}`);
    if (status.active) {
      _crapsEnterGame(status.point);
    } else {
      _crapsShowBet();
    }
  } catch { _crapsShowBet(); }
}

function _crapsShowBet() {
  document.getElementById('crapsBetWrap').style.display = '';
  document.getElementById('crapsGameWrap').style.display = 'none';
  document.getElementById('crapsStatus').textContent = '';
}

function _crapsEnterGame(point) {
  document.getElementById('crapsBetWrap').style.display = 'none';
  document.getElementById('crapsGameWrap').style.display = 'flex';
  document.getElementById('crapsStatus').textContent = point
    ? `Point: ${point} — roll it again before a 7!`
    : 'Come-out roll — hit 7 or 11 to win!';
  document.getElementById('crapsDie1').textContent = '⬜';
  document.getElementById('crapsDie2').textContent = '⬜';
  document.getElementById('crapsTotal').textContent = '';
}

async function startCraps() {
  const bet = parseInt(document.getElementById('crapsBet').value);
  document.getElementById('crapsErr').textContent = '';
  document.getElementById('crapsStartBtn').disabled = true;
  try {
    const data = await api('/api/play/craps/start', {
      method: 'POST', json: { user_id: +state.userId, bet }
    });
    state.balance = data.new_balance;
    document.getElementById('crapsBal').textContent = fmt(state.balance);
    refreshHeaderBal();
    _crapsEnterGame(null);
  } catch (e) {
    document.getElementById('crapsErr').textContent = e.message || 'Error';
  } finally {
    document.getElementById('crapsStartBtn').disabled = false;
  }
}

async function rollCraps() {
  document.getElementById('crapsRollBtn').disabled = true;
  document.getElementById('crapsErr').textContent = '';
  try {
    const data = await api('/api/play/craps/roll', {
      method: 'POST', json: { user_id: +state.userId }
    });
    document.getElementById('crapsDie1').textContent = _DICE_EMOJI[data.d1];
    document.getElementById('crapsDie2').textContent = _DICE_EMOJI[data.d2];
    document.getElementById('crapsTotal').textContent = `Total: ${data.total}`;

    if (data.result === 'win') {
      state.balance = data.new_balance;
      document.getElementById('crapsBal').textContent = fmt(state.balance);
      refreshHeaderBal();
      const resEl = document.getElementById('crapsResult');
      resEl.textContent = `🎉 Winner! +${fmt(data.winnings)} WRK$`;
      resEl.className = 'result-box show win-box';
      _crapsShowBet();
    } else if (data.result === 'lose') {
      state.balance = data.new_balance;
      document.getElementById('crapsBal').textContent = fmt(state.balance);
      refreshHeaderBal();
      const resEl = document.getElementById('crapsResult');
      resEl.textContent = `💀 Seven out. Lost ${fmt(data.lost)} WRK$.`;
      resEl.className = 'result-box show lose-box';
      _crapsShowBet();
    } else if (data.result === 'point') {
      document.getElementById('crapsStatus').textContent = `Point: ${data.point} — roll it again before a 7!`;
    } else {
      document.getElementById('crapsStatus').textContent = `Point: ${data.point} — rolled ${data.total}, keep going!`;
    }
  } catch (e) {
    document.getElementById('crapsErr').textContent = e.message || 'Error';
  } finally {
    document.getElementById('crapsRollBtn').disabled = false;
  }
}
```

- [ ] **Step 3: Run tests + check UI**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Check browser: craps start → come-out roll shows dice → point phase status updates → win/lose resolves.

- [ ] **Step 4: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/static/index.html
git commit -m "feat: craps modal + JS in mini-app"
```

---

## Task 13: Frontend — Hack Modal + JS

**Files:**
- Modify: `miniapp/static/index.html`

- [ ] **Step 1: Add Hack modal HTML**

After the craps modal, insert:

```html
<!-- HACK MODAL -->
<div class="modal-bg" id="hackModal" onclick="closeModal('hackModal',event)">
  <div class="modal" style="position:relative">
    <button class="modal-close" onclick="closeModal('hackModal')">✕</button>
    <div class="modal-title">🖥️ Hack</div>
    <div id="hackCooldown" style="text-align:center;color:var(--muted);font-size:13px;margin-bottom:8px"></div>
    <div id="hackStartWrap" style="text-align:center">
      <div style="color:var(--muted);font-size:13px;margin-bottom:12px">Crack a crypto wallet seed phrase</div>
      <button class="btn" id="hackStartBtn" onclick="startHack()" style="width:100%">🖥️ Start Hack</button>
    </div>
    <div id="hackGameWrap" style="display:none;flex-direction:column;gap:12px">
      <div style="background:var(--card);border-radius:8px;padding:12px;font-size:13px;color:var(--muted)" id="hackClue"></div>
      <div style="text-align:center;font-size:28px;letter-spacing:6px;font-family:monospace;font-weight:700" id="hackDisplay"></div>
      <div style="text-align:center;font-size:13px;color:var(--muted)">Attempts left: <span id="hackAttempts" style="color:var(--text);font-weight:600">5</span></div>
      <div style="text-align:center;font-size:13px;color:var(--gold)">Reward: <span id="hackReward">—</span> WRK$</div>
      <div class="bet-row">
        <input class="input" id="hackGuessInput" type="text" placeholder="Enter your guess…" style="flex:1">
        <button class="btn" id="hackGuessBtn" onclick="submitHackGuess()" style="min-width:80px">GUESS</button>
      </div>
    </div>
    <div class="result-box" id="hackResult"></div>
    <div class="error-msg" id="hackErr"></div>
  </div>
</div>
```

- [ ] **Step 2: Add Hack JS**

Before `function openRoulette()`, add:

```javascript
// ── Hack ──────────────────────────────────────────────────────────────────────
async function openHack() {
  if (!state.userId) return alert('Open in Telegram to play.');
  document.getElementById('hackResult').className = 'result-box';
  document.getElementById('hackErr').textContent = '';
  document.getElementById('hackModal').classList.add('open');
  try {
    const status = await api(`/api/hack/status/${state.userId}`);
    if (status.active) {
      _hackEnterGame(status.display, status.clue, status.attempts, status.reward);
    } else if (status.cooldown_remaining > 0) {
      document.getElementById('hackStartWrap').style.display = 'block';
      document.getElementById('hackGameWrap').style.display = 'none';
      document.getElementById('hackStartBtn').disabled = true;
      const m = Math.floor(status.cooldown_remaining / 60), s = status.cooldown_remaining % 60;
      document.getElementById('hackCooldown').textContent = `⏳ Cooldown: ${m}m ${s}s`;
    } else {
      document.getElementById('hackStartWrap').style.display = 'block';
      document.getElementById('hackGameWrap').style.display = 'none';
      document.getElementById('hackStartBtn').disabled = false;
      document.getElementById('hackCooldown').textContent = '';
    }
  } catch (e) {
    document.getElementById('hackErr').textContent = e.message || 'Error loading status';
  }
}

function _hackEnterGame(display, clue, attempts, reward) {
  document.getElementById('hackStartWrap').style.display = 'none';
  document.getElementById('hackGameWrap').style.display = 'flex';
  document.getElementById('hackDisplay').textContent = display;
  document.getElementById('hackClue').textContent = `Clue: ${clue}`;
  document.getElementById('hackAttempts').textContent = attempts;
  document.getElementById('hackReward').textContent = fmt(reward);
  document.getElementById('hackGuessInput').value = '';
}

async function startHack() {
  document.getElementById('hackStartBtn').disabled = true;
  document.getElementById('hackErr').textContent = '';
  try {
    const data = await api('/api/hack/start', {
      method: 'POST', json: { user_id: +state.userId }
    });
    _hackEnterGame(data.display, data.clue, data.attempts, data.reward);
  } catch (e) {
    document.getElementById('hackErr').textContent = e.message || 'Error';
    document.getElementById('hackStartBtn').disabled = false;
  }
}

async function submitHackGuess() {
  const word = document.getElementById('hackGuessInput').value.trim();
  if (!word) return;
  document.getElementById('hackGuessBtn').disabled = true;
  document.getElementById('hackErr').textContent = '';
  try {
    const data = await api('/api/hack/guess', {
      method: 'POST', json: { user_id: +state.userId, word }
    });
    if (data.result === 'win') {
      state.balance = data.new_balance;
      refreshHeaderBal();
      const resEl = document.getElementById('hackResult');
      resEl.textContent = `✅ ACCESS GRANTED — "${data.word}" +${fmt(data.reward)} WRK$`;
      resEl.className = 'result-box show win-box';
      document.getElementById('hackGameWrap').style.display = 'none';
      document.getElementById('hackStartWrap').style.display = 'block';
      document.getElementById('hackStartBtn').disabled = true;
      document.getElementById('hackCooldown').textContent = '⏳ Cooldown: 60m';
    } else if (data.result === 'lose') {
      const resEl = document.getElementById('hackResult');
      resEl.textContent = `❌ CONNECTION TERMINATED — the word was "${data.word}"`;
      resEl.className = 'result-box show lose-box';
      document.getElementById('hackGameWrap').style.display = 'none';
      document.getElementById('hackStartWrap').style.display = 'block';
      document.getElementById('hackStartBtn').disabled = true;
      document.getElementById('hackCooldown').textContent = '⏳ Cooldown: 60m';
    } else {
      document.getElementById('hackDisplay').textContent = data.display;
      document.getElementById('hackAttempts').textContent = data.attempts_left;
      document.getElementById('hackGuessInput').value = '';
    }
  } catch (e) {
    document.getElementById('hackErr').textContent = e.message || 'Error';
  } finally {
    document.getElementById('hackGuessBtn').disabled = false;
  }
}
```

- [ ] **Step 3: Run tests + check UI**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Check browser: Hack start, guess wrong → letter revealed, guess correct → WIN result, cooldown shown.

- [ ] **Step 4: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/static/index.html
git commit -m "feat: hack modal + JS in mini-app"
```

---

## Task 14: Frontend — Rob Modal + JS

**Files:**
- Modify: `miniapp/static/index.html`

- [ ] **Step 1: Add Rob modal HTML**

After the hack modal, insert:

```html
<!-- ROB MODAL -->
<div class="modal-bg" id="robModal" onclick="closeModal('robModal',event)">
  <div class="modal" style="position:relative;max-height:90vh;overflow-y:auto">
    <button class="modal-close" onclick="closeModal('robModal')">✕</button>
    <div class="modal-title">🔫 Rob</div>
    <div id="robCooldown" style="text-align:center;color:var(--muted);font-size:13px;margin-bottom:8px"></div>
    <div id="robTargetWrap">
      <div style="color:var(--muted);font-size:13px;margin-bottom:8px">Select a target:</div>
      <div id="robTargetList" style="display:flex;flex-direction:column;gap:8px;max-height:280px;overflow-y:auto"></div>
    </div>
    <div id="robConfirmWrap" style="display:none;flex-direction:column;gap:12px">
      <div style="text-align:center;font-size:15px" id="robConfirmText"></div>
      <div style="display:flex;gap:8px">
        <button class="btn outline" onclick="cancelRob()" style="flex:1">Cancel</button>
        <button class="btn" id="robAttemptBtn" onclick="attemptRob()" style="flex:1;background:var(--red)">Rob 'em</button>
      </div>
    </div>
    <div class="result-box" id="robResult"></div>
    <div class="error-msg" id="robErr"></div>
  </div>
</div>
```

- [ ] **Step 2: Add Rob JS**

Before `function openRoulette()`, add:

```javascript
// ── Rob ───────────────────────────────────────────────────────────────────────
let robTargetId = null;
let robTargetName = null;

async function openRob() {
  if (!state.userId) return alert('Open in Telegram to play.');
  robTargetId = null;
  robTargetName = null;
  document.getElementById('robResult').className = 'result-box';
  document.getElementById('robErr').textContent = '';
  document.getElementById('robTargetWrap').style.display = '';
  document.getElementById('robConfirmWrap').style.display = 'none';
  document.getElementById('robModal').classList.add('open');

  // Check cooldown
  try {
    const cd = await api(`/api/rob/cooldown/${state.userId}`);
    if (cd.cooldown_remaining > 0) {
      const m = Math.floor(cd.cooldown_remaining / 60), s = cd.cooldown_remaining % 60;
      document.getElementById('robCooldown').textContent = `⏳ Cooldown: ${m}m ${s}s`;
      document.getElementById('robTargetList').innerHTML = '<div style="color:var(--muted);font-size:13px;text-align:center">On cooldown — come back later.</div>';
      return;
    }
    document.getElementById('robCooldown').textContent = '';
  } catch { }

  // Load targets
  try {
    const targets = await api(`/api/rob/targets?user_id=${state.userId}`);
    const list = document.getElementById('robTargetList');
    if (!targets.length) {
      list.innerHTML = '<div style="color:var(--muted);font-size:13px;text-align:center">No targets with enough WRK$.</div>';
      return;
    }
    list.innerHTML = targets.map(t => `
      <div class="card" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:10px 14px"
           onclick="selectRobTarget(${t.user_id}, '${t.name.replace(/'/g,"\\'")}')">
        <span>${t.name}</span>
        <span style="color:var(--gold);font-size:13px;font-weight:600">${fmt(t.balance)} WRK$</span>
      </div>`).join('');
  } catch (e) {
    document.getElementById('robErr').textContent = e.message || 'Error loading targets';
  }
}

function selectRobTarget(userId, name) {
  robTargetId = userId;
  robTargetName = name;
  document.getElementById('robTargetWrap').style.display = 'none';
  document.getElementById('robConfirmWrap').style.display = 'flex';
  document.getElementById('robConfirmText').textContent = `Rob ${name}?`;
}

function cancelRob() {
  robTargetId = null;
  document.getElementById('robConfirmWrap').style.display = 'none';
  document.getElementById('robTargetWrap').style.display = '';
}

async function attemptRob() {
  if (!robTargetId) return;
  document.getElementById('robAttemptBtn').disabled = true;
  document.getElementById('robErr').textContent = '';
  try {
    const data = await api('/api/rob/attempt', {
      method: 'POST',
      json: { user_id: +state.userId, target_id: robTargetId }
    });
    state.balance = data.new_balance;
    refreshHeaderBal();
    const resEl = document.getElementById('robResult');
    resEl.textContent = `${data.emoji} ${data.flavor}`;
    resEl.className = `result-box show ${data.outcome === 'success' ? 'win-box' : 'lose-box'}`;
    document.getElementById('robConfirmWrap').style.display = 'none';
    document.getElementById('robTargetWrap').style.display = '';
    document.getElementById('robTargetList').innerHTML = `<div style="color:var(--muted);font-size:13px;text-align:center">Balance: ${fmt(data.new_balance)} WRK$</div>`;
  } catch (e) {
    document.getElementById('robErr').textContent = e.message || 'Error';
    document.getElementById('robAttemptBtn').disabled = false;
  }
}
```

- [ ] **Step 3: Run tests + check UI**

```bash
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v
```
Check browser: Rob modal opens, targets list loads, select target → confirm screen, attempt → flavor text result.

- [ ] **Step 4: Commit**

```bash
cd /home/ogkush/Projects/wrkshelperbot
git add miniapp/static/index.html
git commit -m "feat: rob modal + JS in mini-app"
```

---

## Post-Implementation Checklist

After all tasks are complete, run the following to verify the full feature:

```bash
# 1. All tests pass
cd /home/ogkush/Projects/wrkshelperbot && python -m pytest tests/ -v

# 2. Server starts clean
uvicorn miniapp.server:app --port 8420 --reload

# 3. Deploy to Pi
ssh pi "cd ~/wrkshelperbot && git pull && systemctl --user restart wrkshelperbot miniapp"
```

Manual verification checklist:
- [ ] Roulette: all 3 colors work, correct payout on win
- [ ] High-Low: sessions persist across refresh, cash out credits correctly
- [ ] Craps: come-out and point phase both resolve correctly
- [ ] Hack: bot `/hack` and mini-app show same session; cooldown shared
- [ ] Rob: targets list excludes self, victim gets DM, cooldown blocks retry
- [ ] Bot `/rob` and `/hack` still work normally with DB cooldowns
