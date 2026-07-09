# Economy Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global WRK$ economy system to wrkshelperbot with dailies, gambling (slots, coinflip, dice, blackjack, crash), robbery, and a leaderboard.

**Architecture:** A single `handlers/economy.py` file holds all command handlers and in-memory game state (crash, blackjack). All persistent data lives in a new `economy` table in the existing SQLite DB via new functions in `db.py`. Commands are registered in `bot.py`.

**Tech Stack:** python-telegram-bot 21+, aiosqlite, APScheduler (via PTB job_queue), pytest-asyncio

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `db.py` | Modify | Add `economy` table to `_SCHEMA`; add wallet DB functions |
| `handlers/economy.py` | Create | All economy handlers + in-memory crash/blackjack state |
| `tests/test_economy_db.py` | Create | Tests for all DB wallet functions |
| `tests/test_economy_logic.py` | Create | Tests for pure game logic (slots, BJ eval, crash point, rob outcomes) |
| `bot.py` | Modify | Import and register all economy handlers |

---

## Task 1: Economy DB Table and Functions

**Files:**
- Modify: `db.py`
- Create: `tests/test_economy_db.py`

- [ ] **Step 1: Write failing DB tests**

Create `tests/test_economy_db.py`:

```python
import time
import pytest
from db import init_db, upsert_wallet, get_wallet, update_balance, get_leaderboard, set_daily

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")

@pytest.mark.asyncio
async def test_upsert_wallet_creates_with_1000(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    w = await get_wallet(db_path, 111)
    assert w["balance"] == 1000
    assert w["streak"] == 0

@pytest.mark.asyncio
async def test_upsert_wallet_idempotent(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    await update_balance(db_path, 111, 500)
    await upsert_wallet(db_path, 111, "alice_new", "Alice New")
    w = await get_wallet(db_path, 111)
    assert w["balance"] == 1500  # balance preserved
    assert w["full_name"] == "Alice New"  # name updated

@pytest.mark.asyncio
async def test_update_balance_add(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    new_bal = await update_balance(db_path, 111, 200)
    assert new_bal == 1200

@pytest.mark.asyncio
async def test_update_balance_subtract(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    new_bal = await update_balance(db_path, 111, -500)
    assert new_bal == 500

@pytest.mark.asyncio
async def test_get_wallet_none_for_unknown(db_path):
    await init_db(db_path)
    w = await get_wallet(db_path, 999)
    assert w is None

@pytest.mark.asyncio
async def test_leaderboard_order(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 1, "a", "A")
    await upsert_wallet(db_path, 2, "b", "B")
    await upsert_wallet(db_path, 3, "c", "C")
    await update_balance(db_path, 1, 5000)
    await update_balance(db_path, 3, 2000)
    rows = await get_leaderboard(db_path, limit=10)
    assert rows[0]["user_id"] == 1
    assert rows[1]["user_id"] == 3

@pytest.mark.asyncio
async def test_set_daily_updates_streak(db_path):
    await init_db(db_path)
    await upsert_wallet(db_path, 111, "alice", "Alice")
    now = int(time.time())
    await set_daily(db_path, 111, streak=3, timestamp=now)
    w = await get_wallet(db_path, 111)
    assert w["streak"] == 3
    assert w["last_daily"] == now
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -m pytest tests/test_economy_db.py -v 2>&1 | head -30
```
Expected: ImportError or AttributeError (functions don't exist yet)

- [ ] **Step 3: Add economy table to `_SCHEMA` in `db.py`**

Add after the last `CREATE TABLE` block (before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS economy (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT,
    balance     INTEGER NOT NULL DEFAULT 1000,
    streak      INTEGER NOT NULL DEFAULT 0,
    last_daily  INTEGER NOT NULL DEFAULT 0
);
```

- [ ] **Step 4: Add wallet functions to `db.py`**

Append after the existing `get_user_by_username` function:

```python
# --- economy ---

async def upsert_wallet(db_path: str, user_id: int, username: str | None, full_name: str | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO economy (user_id, username, full_name, balance, streak, last_daily)
               VALUES (?, ?, ?, 1000, 0, 0)
               ON CONFLICT(user_id) DO UPDATE SET
                   username = excluded.username,
                   full_name = excluded.full_name""",
            (user_id, username, full_name),
        )
        await db.commit()


async def get_wallet(db_path: str, user_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, full_name, balance, streak, last_daily FROM economy WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_balance(db_path: str, user_id: int, delta: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "UPDATE economy SET balance = balance + ? WHERE user_id = ? RETURNING balance",
            (delta, user_id),
        ) as cur:
            row = await cur.fetchone()
            await db.commit()
            return row[0] if row else 0


async def get_leaderboard(db_path: str, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, full_name, username, balance FROM economy ORDER BY balance DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) async for r in cur]


async def set_daily(db_path: str, user_id: int, streak: int, timestamp: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE economy SET streak = ?, last_daily = ? WHERE user_id = ?",
            (streak, timestamp, user_id),
        )
        await db.commit()
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
python -m pytest tests/test_economy_db.py -v
```
Expected: all 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_economy_db.py
git commit -m "feat: economy DB table and wallet functions"
```

---

## Task 2: Pure Game Logic + Tests

**Files:**
- Create: `tests/test_economy_logic.py`

All pure functions will live in `handlers/economy.py` (created in Task 3). Write the tests now so Task 3 is TDD.

- [ ] **Step 1: Create `tests/test_economy_logic.py`**

```python
import pytest
from handlers.economy import (
    _daily_streak_multiplier,
    _slots_result,
    _bj_hand_value,
    _bj_is_blackjack,
    _generate_crash_point,
    _crash_multiplier,
    _rob_outcome,
)


# --- Daily ---

def test_streak_multiplier_days_1_to_6():
    for d in range(1, 7):
        assert _daily_streak_multiplier(d) == 1

def test_streak_multiplier_day_7():
    assert _daily_streak_multiplier(7) == 2

def test_streak_multiplier_day_14():
    assert _daily_streak_multiplier(14) == 3

def test_streak_multiplier_day_30_plus():
    assert _daily_streak_multiplier(30) == 4
    assert _daily_streak_multiplier(99) == 4


# --- Slots ---

def test_slots_three_sevens():
    outcome, multiplier = _slots_result(['7️⃣', '7️⃣', '7️⃣'])
    assert outcome == "jackpot"
    assert multiplier == 50

def test_slots_three_match_non_seven():
    outcome, multiplier = _slots_result(['🍒', '🍒', '🍒'])
    assert outcome == "three_match"
    assert multiplier == 10

def test_slots_two_match():
    outcome, multiplier = _slots_result(['🍒', '🍒', '🍋'])
    assert outcome == "two_match"
    assert multiplier == 2

def test_slots_no_match():
    outcome, multiplier = _slots_result(['🍒', '🍋', '🔔'])
    assert outcome == "no_match"
    assert multiplier == 0


# --- Blackjack ---

def test_bj_hand_value_simple():
    assert _bj_hand_value([('10', '♠'), ('7', '♥')]) == 17

def test_bj_hand_value_face_cards():
    assert _bj_hand_value([('K', '♠'), ('Q', '♥')]) == 20

def test_bj_hand_value_ace_high():
    assert _bj_hand_value([('A', '♠'), ('9', '♥')]) == 20

def test_bj_hand_value_ace_low_to_avoid_bust():
    assert _bj_hand_value([('A', '♠'), ('9', '♥'), ('5', '♣')]) == 15

def test_bj_hand_value_bust():
    assert _bj_hand_value([('K', '♠'), ('Q', '♥'), ('5', '♣')]) == 25

def test_bj_is_blackjack_true():
    assert _bj_is_blackjack([('A', '♠'), ('K', '♥')]) is True

def test_bj_is_blackjack_false_three_cards():
    assert _bj_is_blackjack([('A', '♠'), ('5', '♥'), ('5', '♣')]) is False

def test_bj_is_blackjack_false_21_not_natural():
    assert _bj_is_blackjack([('7', '♠'), ('7', '♥'), ('7', '♣')]) is False


# --- Crash ---

def test_generate_crash_point_range():
    for _ in range(200):
        point = _generate_crash_point()
        assert 1.0 <= point <= 2500.0

def test_crash_multiplier_starts_at_one():
    assert _crash_multiplier(0) == 1.0

def test_crash_multiplier_grows():
    assert _crash_multiplier(10) > _crash_multiplier(5)


# --- Rob ---

def test_rob_outcome_success_range():
    for _ in range(100):
        result = _rob_outcome(success=True, robber_balance=1000, victim_balance=1000)
        assert result["outcome"] == "success"
        stolen = result["amount"]
        assert 30 <= stolen <= 100  # 3-10% of 1000

def test_rob_outcome_fail_variants():
    outcomes = set()
    for _ in range(500):
        result = _rob_outcome(success=False, robber_balance=1000, victim_balance=1000)
        outcomes.add(result["outcome"])
    assert "fine" in outcomes
    assert "bail" in outcomes
    assert "getaway" in outcomes
```

- [ ] **Step 2: Run to confirm all fail (ImportError)**

```bash
python -m pytest tests/test_economy_logic.py -v 2>&1 | head -10
```
Expected: `ImportError: cannot import name '_daily_streak_multiplier' from 'handlers.economy'`

- [ ] **Step 3: Commit the test file**

```bash
git add tests/test_economy_logic.py
git commit -m "test: economy game logic tests (all failing — TDD)"
```

---

## Task 3: Create `handlers/economy.py` — Core Logic + Balance/Daily/Leaderboard

**Files:**
- Create: `handlers/economy.py`

- [ ] **Step 1: Create `handlers/economy.py` with pure logic functions and `/balance`, `/daily`, `/leaderboard`**

```python
import math
import random
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import display_name

log = logging.getLogger(__name__)

# ── In-memory game state ──────────────────────────────────────────────────────
_crash_games: dict[int, dict] = {}   # chat_id -> game state
_bj_games: dict[int, dict] = {}      # user_id -> game state

SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
SLOT_SYMBOLS = ['🍒', '🍊', '🍋', '🔔', '⭐', '💎', '7️⃣']


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _daily_streak_multiplier(streak: int) -> int:
    if streak >= 30:
        return 4
    if streak >= 14:
        return 3
    if streak >= 7:
        return 2
    return 1


def _slots_result(reels: list[str]) -> tuple[str, int]:
    if reels == ['7️⃣', '7️⃣', '7️⃣']:
        return "jackpot", 50
    if reels[0] == reels[1] == reels[2]:
        return "three_match", 10
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        return "two_match", 2
    return "no_match", 0


def _bj_hand_value(hand: list[tuple[str, str]]) -> int:
    value = 0
    aces = 0
    for rank, _ in hand:
        if rank == 'A':
            aces += 1
            value += 11
        elif rank in ('J', 'Q', 'K'):
            value += 10
        else:
            value += int(rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def _bj_is_blackjack(hand: list[tuple[str, str]]) -> bool:
    return len(hand) == 2 and _bj_hand_value(hand) == 21


def _generate_crash_point() -> float:
    r = random.random()
    if r < 0.50:
        return round(random.uniform(1.0, 2.0), 2)
    elif r < 0.75:
        return round(random.uniform(2.0, 5.0), 2)
    elif r < 0.90:
        return round(random.uniform(5.0, 20.0), 2)
    elif r < 0.98:
        return round(random.uniform(20.0, 100.0), 2)
    else:
        return round(random.uniform(100.0, 2500.0), 2)


def _crash_multiplier(ticks: int) -> float:
    return round(math.pow(1.06, ticks), 2)


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


def _new_deck() -> list[tuple[str, str]]:
    deck = [(r, s) for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def _fmt(wallet: dict) -> str:
    return f"💰 {wallet['balance']:,} WRK$"


async def _ensure_wallet(user: object, db_path: str) -> dict:
    await db.upsert_wallet(db_path, user.id, user.username, user.full_name)
    return await db.get_wallet(db_path, user.id)


# ── /balance ──────────────────────────────────────────────────────────────────

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)
    streak = wallet["streak"]
    mult = _daily_streak_multiplier(streak)
    streak_line = f"🔥 Streak: {streak} day(s)"
    if mult > 1:
        streak_line += f" (daily bonus: {mult}x)"
    await update.effective_message.reply_text(
        f"{_fmt(wallet)}\n{streak_line}"
    )


# ── /daily ────────────────────────────────────────────────────────────────────

async def cmd_daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)
    now = int(time.time())
    last = wallet["last_daily"]
    cooldown = 86400  # 24 hours

    if now - last < cooldown:
        remaining = cooldown - (now - last)
        h, m = divmod(remaining // 60, 60)
        await update.effective_message.reply_text(
            f"⏳ Daily already claimed. Next claim in {h}h {m}m."
        )
        return

    # Streak logic: if more than 48h since last claim, reset streak
    streak = wallet["streak"]
    if last > 0 and now - last > 172800:
        streak = 0
    streak += 1

    base = random.randint(500, 1500)
    mult = _daily_streak_multiplier(streak)
    earned = base * mult

    await db.update_balance(config.DB_PATH, user.id, earned)
    await db.set_daily(config.DB_PATH, user.id, streak=streak, timestamp=now)
    new_wallet = await db.get_wallet(config.DB_PATH, user.id)

    bonus_note = f" (streak {mult}x bonus!)" if mult > 1 else ""
    next_milestone = ""
    if streak < 7:
        next_milestone = f"\n📅 {7 - streak} day(s) until 2x daily bonus"
    elif streak < 14:
        next_milestone = f"\n📅 {14 - streak} day(s) until 3x daily bonus"
    elif streak < 30:
        next_milestone = f"\n📅 {30 - streak} day(s) until 4x daily bonus"

    await update.effective_message.reply_text(
        f"✅ Daily claimed! +{earned:,} WRK${bonus_note}\n"
        f"🔥 Streak: {streak} day(s)\n"
        f"{_fmt(new_wallet)}{next_milestone}"
    )


# ── /leaderboard ──────────────────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = await db.get_leaderboard(config.DB_PATH, limit=10)
    if not rows:
        await update.effective_message.reply_text("No one has a wallet yet.")
        return
    medals = ['🥇', '🥈', '🥉']
    lines = ["🏆 *WRK$ Leaderboard*\n"]
    for i, row in enumerate(rows):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        name = row.get("full_name") or row.get("username") or str(row["user_id"])
        lines.append(f"{prefix} {name} — {row['balance']:,} WRK$")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")
```

- [ ] **Step 2: Run logic tests — they should now pass**

```bash
python -m pytest tests/test_economy_logic.py -v
```
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add handlers/economy.py
git commit -m "feat: economy core logic + balance/daily/leaderboard commands"
```

---

## Task 4: Rob Command

**Files:**
- Modify: `handlers/economy.py`

- [ ] **Step 1: Add `cmd_rob` to `handlers/economy.py`**

Append after `cmd_leaderboard`:

```python
# ── /rob ──────────────────────────────────────────────────────────────────────

_rob_cooldowns: dict[int, float] = {}  # user_id -> timestamp

async def cmd_rob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    robber = update.effective_user
    robber_wallet = await _ensure_wallet(robber, config.DB_PATH)

    # Cooldown check
    now = time.time()
    last_rob = _rob_cooldowns.get(robber.id, 0)
    if now - last_rob < 3600:
        remaining = int(3600 - (now - last_rob))
        m = remaining // 60
        await msg.reply_text(f"⏳ Rob cooldown: {m}m remaining.")
        return

    # Resolve target
    if not ctx.args:
        await msg.reply_text("Usage: `/rob @username`", parse_mode="Markdown")
        return

    target_username = ctx.args[0]
    target_row = await db.get_user_by_username(config.DB_PATH, msg.chat.id, target_username)
    if not target_row:
        await msg.reply_text("❌ Can't find that user. They need to have sent a message first.")
        return

    target_id = target_row["user_id"]
    target_name = target_row["full_name"] or target_username

    if target_id == robber.id:
        await msg.reply_text("❌ You can't rob yourself.")
        return

    target_wallet = await db.get_wallet(config.DB_PATH, target_id)
    if not target_wallet or target_wallet["balance"] < 500:
        await msg.reply_text(f"❌ {target_name} doesn't have enough WRK$ to rob (minimum 500).")
        return

    _rob_cooldowns[robber.id] = now
    success = random.random() < 0.50
    result = _rob_outcome(success, robber_wallet["balance"], target_wallet["balance"])

    if result["outcome"] == "success":
        amount = result["amount"]
        await db.update_balance(config.DB_PATH, target_id, -amount)
        await db.update_balance(config.DB_PATH, robber.id, amount)
        new_bal = (await db.get_wallet(config.DB_PATH, robber.id))["balance"]
        await msg.reply_text(
            f"🥷 Success! You robbed {target_name} for {amount:,} WRK$.\n"
            f"💰 Your balance: {new_bal:,} WRK$"
        )
    elif result["outcome"] == "fine":
        amount = min(result["amount"], robber_wallet["balance"])
        await db.update_balance(config.DB_PATH, robber.id, -amount)
        new_bal = (await db.get_wallet(config.DB_PATH, robber.id))["balance"]
        await msg.reply_text(
            f"🚔 You got chased off! Lost {amount:,} WRK$ running away.\n"
            f"💰 Your balance: {new_bal:,} WRK$"
        )
    elif result["outcome"] == "bail":
        amount = min(result["amount"], robber_wallet["balance"])
        await db.update_balance(config.DB_PATH, robber.id, -amount)
        new_bal = (await db.get_wallet(config.DB_PATH, robber.id))["balance"]
        await msg.reply_text(
            f"🚨 Busted! You were arrested and had to bail out. Lost {amount:,} WRK$.\n"
            f"💰 Your balance: {new_bal:,} WRK$"
        )
    else:  # getaway
        await msg.reply_text(
            f"😮‍💨 You failed the rob but made a clean getaway. No loss.\n"
            f"💰 Your balance: {robber_wallet['balance']:,} WRK$"
        )
```

- [ ] **Step 2: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v 2>&1 | tail -15
```
Expected: all existing tests still pass

- [ ] **Step 3: Commit**

```bash
git add handlers/economy.py
git commit -m "feat: /rob command with cooldown and three fail outcomes"
```

---

## Task 5: Slots, Coinflip, Dice

**Files:**
- Modify: `handlers/economy.py`

- [ ] **Step 1: Add three gambling commands to `handlers/economy.py`**

Append after `cmd_rob`:

```python
# ── /slots ────────────────────────────────────────────────────────────────────

async def cmd_slots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    if not ctx.args or not ctx.args[0].isdigit():
        await msg.reply_text("Usage: `/slots <bet>`", parse_mode="Markdown")
        return
    bet = int(ctx.args[0])
    if bet < 10:
        await msg.reply_text("❌ Minimum bet is 10 WRK$.")
        return
    if wallet["balance"] < bet:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

    reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    outcome, mult = _slots_result(reels)
    display = " | ".join(reels)

    if outcome == "no_match":
        await db.update_balance(config.DB_PATH, user.id, -bet)
        new_bal = wallet["balance"] - bet
        await msg.reply_text(f"🎰 {display}\n\nNo match. Lost {bet:,} WRK$.\n💰 {new_bal:,} WRK$")
    else:
        winnings = bet * mult - bet
        await db.update_balance(config.DB_PATH, user.id, winnings)
        new_bal = wallet["balance"] + winnings
        label = {"jackpot": "🎉 JACKPOT!", "three_match": "Three of a kind!", "two_match": "Two of a kind!"}[outcome]
        await msg.reply_text(
            f"🎰 {display}\n\n{label} {mult}x → +{winnings:,} WRK$\n💰 {new_bal:,} WRK$"
        )


# ── /coinflip ─────────────────────────────────────────────────────────────────

async def cmd_coinflip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    if not ctx.args or not ctx.args[0].isdigit():
        await msg.reply_text("Usage: `/coinflip <bet> [heads|tails]`", parse_mode="Markdown")
        return
    bet = int(ctx.args[0])
    if bet < 10:
        await msg.reply_text("❌ Minimum bet is 10 WRK$.")
        return
    if wallet["balance"] < bet:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

    pick = ctx.args[1].lower() if len(ctx.args) > 1 and ctx.args[1].lower() in ("heads", "tails") else None
    result = random.choice(["heads", "tails"])
    coin_emoji = "🪙"
    result_label = f"**{result.capitalize()}**"

    won = random.random() < 0.50
    if won:
        await db.update_balance(config.DB_PATH, user.id, bet)
        new_bal = wallet["balance"] + bet
        pick_line = f"You picked {pick}. " if pick else ""
        await msg.reply_text(
            f"{coin_emoji} {result_label}\n\n{pick_line}You won! +{bet:,} WRK$\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )
    else:
        await db.update_balance(config.DB_PATH, user.id, -bet)
        new_bal = wallet["balance"] - bet
        pick_line = f"You picked {pick}. " if pick else ""
        await msg.reply_text(
            f"{coin_emoji} {result_label}\n\n{pick_line}You lost! -{bet:,} WRK$\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )


# ── /dice ─────────────────────────────────────────────────────────────────────

async def cmd_dice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    if not ctx.args or not ctx.args[0].isdigit():
        await msg.reply_text("Usage: `/dice <bet>`", parse_mode="Markdown")
        return
    bet = int(ctx.args[0])
    if bet < 10:
        await msg.reply_text("❌ Minimum bet is 10 WRK$.")
        return
    if wallet["balance"] < bet:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

    player_roll = random.randint(1, 6)
    bot_roll = random.randint(1, 6)

    if player_roll >= bot_roll:
        winnings = int(bet * 0.8)
        await db.update_balance(config.DB_PATH, user.id, winnings)
        new_bal = wallet["balance"] + winnings
        await msg.reply_text(
            f"🎲 You rolled {player_roll} | Bot rolled {bot_roll}\n\nYou win! +{winnings:,} WRK$\n💰 {new_bal:,} WRK$"
        )
    else:
        await db.update_balance(config.DB_PATH, user.id, -bet)
        new_bal = wallet["balance"] - bet
        await msg.reply_text(
            f"🎲 You rolled {player_roll} | Bot rolled {bot_roll}\n\nBot wins. -{bet:,} WRK$\n💰 {new_bal:,} WRK$"
        )
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -10
```
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add handlers/economy.py
git commit -m "feat: /slots, /coinflip, /dice gambling commands"
```

---

## Task 6: Blackjack

**Files:**
- Modify: `handlers/economy.py`

- [ ] **Step 1: Add blackjack command and callback to `handlers/economy.py`**

Append after `cmd_dice`:

```python
# ── /blackjack ────────────────────────────────────────────────────────────────

def _bj_render(player_hand, dealer_hand, hide_dealer=True) -> str:
    def fmt_hand(hand):
        return " ".join(f"{r}{s}" for r, s in hand)
    dealer_display = f"{dealer_hand[0][0]}{dealer_hand[0][1]} ??" if hide_dealer else fmt_hand(dealer_hand)
    return (
        f"🃏 *Blackjack*\n\n"
        f"Your hand: {fmt_hand(player_hand)} = **{_bj_hand_value(player_hand)}**\n"
        f"Dealer: {dealer_display}"
    )


def _bj_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👊 Hit", callback_data=f"bj:hit:{user_id}"),
        InlineKeyboardButton("✋ Stand", callback_data=f"bj:stand:{user_id}"),
    ]])


async def cmd_blackjack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    if user.id in _bj_games:
        await msg.reply_text("❌ You already have an active blackjack game. Finish it first.")
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await msg.reply_text("Usage: `/blackjack <bet>`", parse_mode="Markdown")
        return
    bet = int(ctx.args[0])
    if bet < 10:
        await msg.reply_text("❌ Minimum bet is 10 WRK$.")
        return
    if wallet["balance"] < bet:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

    deck = _new_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    _bj_games[user.id] = {
        "bet": bet,
        "deck": deck,
        "player": player,
        "dealer": dealer,
        "chat_id": msg.chat.id,
    }

    # Immediate blackjack check
    if _bj_is_blackjack(player):
        del _bj_games[user.id]
        winnings = int(bet * 1.5)
        await db.update_balance(config.DB_PATH, user.id, winnings)
        new_bal = wallet["balance"] + winnings
        await msg.reply_text(
            f"{_bj_render(player, dealer, hide_dealer=False)}\n\n"
            f"🎉 Blackjack! +{winnings:,} WRK$\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )
        return

    sent = await msg.reply_text(
        _bj_render(player, dealer),
        parse_mode="Markdown",
        reply_markup=_bj_keyboard(user.id)
    )
    _bj_games[user.id]["message_id"] = sent.message_id


async def blackjack_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, action, uid_str = query.data.split(":")
    user_id = int(uid_str)

    if query.from_user.id != user_id:
        await query.answer("This isn't your game.", show_alert=True)
        return

    game = _bj_games.get(user_id)
    if not game:
        await query.edit_message_text("Game expired.")
        return

    wallet = await db.get_wallet(config.DB_PATH, user_id)
    bet = game["bet"]

    if action == "hit":
        game["player"].append(game["deck"].pop())
        val = _bj_hand_value(game["player"])
        if val > 21:
            del _bj_games[user_id]
            await db.update_balance(config.DB_PATH, user_id, -bet)
            new_bal = wallet["balance"] - bet
            await query.edit_message_text(
                f"{_bj_render(game['player'], game['dealer'], hide_dealer=False)}\n\n"
                f"💥 Bust! Lost {bet:,} WRK$\n💰 {new_bal:,} WRK$",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                _bj_render(game["player"], game["dealer"]),
                parse_mode="Markdown",
                reply_markup=_bj_keyboard(user_id)
            )

    elif action == "stand":
        dealer_hand = game["dealer"]
        deck = game["deck"]
        while _bj_hand_value(dealer_hand) < 17:
            dealer_hand.append(deck.pop())

        player_val = _bj_hand_value(game["player"])
        dealer_val = _bj_hand_value(dealer_hand)
        del _bj_games[user_id]

        if dealer_val > 21 or player_val > dealer_val:
            await db.update_balance(config.DB_PATH, user_id, bet)
            new_bal = wallet["balance"] + bet
            result = f"🏆 You win! +{bet:,} WRK$"
        elif player_val == dealer_val:
            result = f"🤝 Push — bet returned."
            new_bal = wallet["balance"]
        else:
            await db.update_balance(config.DB_PATH, user_id, -bet)
            new_bal = wallet["balance"] - bet
            result = f"😞 Dealer wins. -{bet:,} WRK$"

        await query.edit_message_text(
            f"{_bj_render(game['player'], dealer_hand, hide_dealer=False)}\n\n"
            f"{result}\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -10
```
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add handlers/economy.py
git commit -m "feat: /blackjack with Hit/Stand inline buttons"
```

---

## Task 7: Crash Game

**Files:**
- Modify: `handlers/economy.py`

- [ ] **Step 1: Add crash game to `handlers/economy.py`**

Append after `blackjack_callback`:

```python
# ── /crash ────────────────────────────────────────────────────────────────────

async def cmd_crash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat_id = msg.chat.id
    wallet = await _ensure_wallet(user, config.DB_PATH)

    if not ctx.args or not ctx.args[0].isdigit():
        await msg.reply_text("Usage: `/crash <bet>`", parse_mode="Markdown")
        return
    bet = int(ctx.args[0])
    if bet < 10:
        await msg.reply_text("❌ Minimum bet is 10 WRK$.")
        return
    if wallet["balance"] < bet:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

    # Joining an active countdown game
    if chat_id in _crash_games:
        game = _crash_games[chat_id]
        if game["state"] != "joining":
            await msg.reply_text("❌ Crash is already in progress, wait for next round.")
            return
        if user.id in game["players"]:
            await msg.reply_text("❌ You're already in this game.")
            return
        game["players"][user.id] = {"bet": bet, "name": display_name(user), "cashed_out": False, "cash_out_mult": None}
        await db.update_balance(config.DB_PATH, user.id, -bet)
        await msg.reply_text(f"✅ Joined crash with {bet:,} WRK$ bet!")
        return

    # Start new crash game
    crash_point = _generate_crash_point()
    _crash_games[chat_id] = {
        "state": "joining",
        "crash_point": crash_point,
        "ticks": 0,
        "players": {
            user.id: {"bet": bet, "name": display_name(user), "cashed_out": False, "cash_out_mult": None}
        },
        "announcement_id": None,
        "live_msg_id": None,
    }
    await db.update_balance(config.DB_PATH, user.id, -bet)

    sent = await msg.reply_text(
        f"🚀 *{display_name(user)} started Crash!*\n"
        f"Type `/crash <bet>` to join.\n\n"
        f"Starting in 10...",
        parse_mode="Markdown"
    )
    _crash_games[chat_id]["announcement_id"] = sent.message_id

    ctx.application.job_queue.run_repeating(
        _crash_countdown_tick,
        interval=1,
        first=1,
        data={"chat_id": chat_id, "tick": 0, "announcement_id": sent.message_id},
        name=f"crash_countdown_{chat_id}",
    )


async def _crash_countdown_tick(ctx: ContextTypes.DEFAULT_TYPE):
    data = ctx.job.data
    chat_id = data["chat_id"]
    data["tick"] += 1
    remaining = 10 - data["tick"]

    game = _crash_games.get(chat_id)
    if not game:
        ctx.job.schedule_removal()
        return

    if remaining > 0:
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=data["announcement_id"],
                text=(
                    f"🚀 *Crash starting soon!*\n"
                    f"Type `/crash <bet>` to join.\n\n"
                    f"Starting in {remaining}..."
                ),
                parse_mode="Markdown"
            )
        except TelegramError:
            pass
        return

    # Countdown done — start the game
    ctx.job.schedule_removal()
    game["state"] = "running"

    player_list = "\n".join(
        f"  • {p['name']} ({p['bet']:,} WRK$)" for p in game["players"].values()
    )
    sent = await ctx.bot.send_message(
        chat_id=chat_id,
        text=f"🚀 *CRASH IS LIVE!*\n\nMultiplier: **1.00x**\n\nPlayers:\n{player_list}\n\nType /cashout to lock in!",
        parse_mode="Markdown"
    )
    game["live_msg_id"] = sent.message_id

    ctx.application.job_queue.run_repeating(
        _crash_game_tick,
        interval=1.5,
        first=1.5,
        data={"chat_id": chat_id},
        name=f"crash_tick_{chat_id}",
    )


async def _crash_game_tick(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = ctx.job.data["chat_id"]
    game = _crash_games.get(chat_id)
    if not game or game["state"] != "running":
        ctx.job.schedule_removal()
        return

    game["ticks"] += 1
    mult = _crash_multiplier(game["ticks"])

    if mult >= game["crash_point"]:
        ctx.job.schedule_removal()
        await _crash_end(ctx.bot, chat_id, game, crashed_at=game["crash_point"])
        return

    active = [p for p in game["players"].values() if not p["cashed_out"]]
    if not active:
        ctx.job.schedule_removal()
        await _crash_end(ctx.bot, chat_id, game, crashed_at=mult)
        return

    active_lines = "\n".join(f"  • {p['name']} ({p['bet']:,} WRK$)" for p in active)
    cashed_lines = "\n".join(
        f"  ✅ {p['name']} cashed @ {p['cash_out_mult']}x"
        for p in game["players"].values() if p["cashed_out"]
    )
    body = f"🚀 *CRASH LIVE — {mult}x*\n\nIn:\n{active_lines}"
    if cashed_lines:
        body += f"\n\nCashed out:\n{cashed_lines}"
    body += "\n\nType /cashout to lock in!"

    try:
        await ctx.bot.edit_message_text(
            chat_id=chat_id,
            message_id=game["live_msg_id"],
            text=body,
            parse_mode="Markdown"
        )
    except TelegramError:
        pass


async def cmd_cashout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat_id = msg.chat.id

    game = _crash_games.get(chat_id)
    if not game or game["state"] != "running":
        await msg.reply_text("No crash game running right now.")
        return

    player = game["players"].get(user.id)
    if not player:
        await msg.reply_text("You're not in this crash game.")
        return
    if player["cashed_out"]:
        await msg.reply_text("You've already cashed out.")
        return

    mult = _crash_multiplier(game["ticks"])
    winnings = int(player["bet"] * mult)
    player["cashed_out"] = True
    player["cash_out_mult"] = mult

    await db.update_balance(config.DB_PATH, user.id, winnings)
    wallet = await db.get_wallet(config.DB_PATH, user.id)
    await msg.reply_text(
        f"💰 Cashed out @ {mult}x! +{winnings:,} WRK$\n"
        f"Balance: {wallet['balance']:,} WRK$"
    )


async def _crash_end(bot, chat_id: int, game: dict, crashed_at: float):
    game["state"] = "crashed"
    lines = ["💥 *CRASHED @ {:.2f}x*\n".format(crashed_at)]
    for uid, p in game["players"].items():
        if p["cashed_out"]:
            profit = int(p["bet"] * p["cash_out_mult"]) - p["bet"]
            lines.append(f"✅ {p['name']} — cashed @ {p['cash_out_mult']}x (+{profit:,} WRK$)")
        else:
            lines.append(f"💀 {p['name']} — lost {p['bet']:,} WRK$")

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=game["live_msg_id"],
            text="\n".join(lines),
            parse_mode="Markdown"
        )
    except TelegramError:
        await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")

    del _crash_games[chat_id]
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -10
```
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add handlers/economy.py
git commit -m "feat: multiplayer /crash game with countdown and /cashout"
```

---

## Task 8: Register All Commands in `bot.py`

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add economy imports to `bot.py`**

Add after the last `from handlers.*` import block:

```python
from handlers.economy import (
    cmd_balance, cmd_daily, cmd_leaderboard,
    cmd_rob, cmd_slots, cmd_coinflip, cmd_dice,
    cmd_blackjack, blackjack_callback,
    cmd_crash, cmd_cashout,
)
```

- [ ] **Step 2: Register handlers in `build_app()` in `bot.py`**

Add after the last `app.add_handler(CommandHandler(...))` block, before the `CallbackQueryHandler` lines:

```python
    app.add_handler(CommandHandler("balance",     cmd_balance))
    app.add_handler(CommandHandler("daily",       cmd_daily))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("rob",         cmd_rob))
    app.add_handler(CommandHandler("slots",       cmd_slots))
    app.add_handler(CommandHandler("coinflip",    cmd_coinflip))
    app.add_handler(CommandHandler("dice",        cmd_dice))
    app.add_handler(CommandHandler("blackjack",   cmd_blackjack))
    app.add_handler(CommandHandler("crash",       cmd_crash))
    app.add_handler(CommandHandler("cashout",     cmd_cashout))
```

Add `blackjack_callback` alongside the existing `CallbackQueryHandler` lines:

```python
    app.add_handler(CallbackQueryHandler(blackjack_callback, pattern=r"^bj:"))
```

- [ ] **Step 3: Smoke test — verify bot starts without import errors**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -c "from bot import build_app; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -15
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: register economy commands in bot.py"
```

---

## Task 9: Deploy to Pi

- [ ] **Step 1: Push to GitHub**

```bash
git push
```

- [ ] **Step 2: Pull and restart on Pi**

```bash
cd ~/wrkshelperbot && git pull && systemctl --user restart wrkshelperbot
```

- [ ] **Step 3: Smoke test in Telegram**

- `/balance` → shows 1,000 WRK$ (new wallet)
- `/daily` → claims reward, shows streak
- `/slots 100` → spins and responds
- `/coinflip 100` → 50/50 result
- `/dice 100` → rolls vs bot
- `/blackjack 100` → card game with Hit/Stand buttons
- `/crash 100` → starts game, second user joins with `/crash 200`, both see live multiplier, `/cashout` works
- `/rob @someone` → rob attempt with outcome message
- `/leaderboard` → top 10 list
