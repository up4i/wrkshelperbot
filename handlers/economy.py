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

    streak = wallet["streak"]
    if last > 0 and now - last > 172800:
        streak = 0
    streak += 1

    base = random.randint(500, 1500)
    mult = _daily_streak_multiplier(streak)
    earned = base * mult

    new_balance = await db.claim_daily(config.DB_PATH, user.id, amount=earned, streak=streak, timestamp=now)

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
        f"💰 {new_balance:,} WRK${next_milestone}"
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


# ── /rob ──────────────────────────────────────────────────────────────────────

_rob_cooldowns: dict[int, float] = {}  # user_id -> timestamp

async def cmd_rob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    robber = update.effective_user
    robber_wallet = await _ensure_wallet(robber, config.DB_PATH)

    now = time.time()
    last_rob = _rob_cooldowns.get(robber.id, 0)
    if now - last_rob < 3600:
        remaining = int(3600 - (now - last_rob))
        m = remaining // 60
        await msg.reply_text(f"⏳ Rob cooldown: {m}m remaining.")
        return

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
        new_bal = await db.update_balance(config.DB_PATH, robber.id, amount)
        await msg.reply_text(
            f"🥷 Success! You robbed {target_name} for {amount:,} WRK$.\n"
            f"💰 Your balance: {new_bal:,} WRK$"
        )
    elif result["outcome"] == "fine":
        amount = result["amount"]
        new_bal = await db.update_balance(config.DB_PATH, robber.id, -amount)
        await msg.reply_text(
            f"🚔 You got chased off! Lost {amount:,} WRK$ running away.\n"
            f"💰 Your balance: {new_bal:,} WRK$"
        )
    elif result["outcome"] == "bail":
        amount = result["amount"]
        new_bal = await db.update_balance(config.DB_PATH, robber.id, -amount)
        await msg.reply_text(
            f"🚨 Busted! You were arrested and had to bail out. Lost {amount:,} WRK$.\n"
            f"💰 Your balance: {new_bal:,} WRK$"
        )
    else:  # getaway
        await msg.reply_text(
            f"😮‍💨 You failed the rob but made a clean getaway. No loss.\n"
            f"💰 Your balance: {robber_wallet['balance']:,} WRK$"
        )


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
        new_bal = await db.update_balance(config.DB_PATH, user.id, -bet)
        await msg.reply_text(f"🎰 {display}\n\nNo match. Lost {bet:,} WRK$.\n💰 {new_bal:,} WRK$")
    else:
        winnings = bet * mult - bet
        new_bal = await db.update_balance(config.DB_PATH, user.id, winnings)
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

    won = random.random() < 0.50
    if won:
        new_bal = await db.update_balance(config.DB_PATH, user.id, bet)
        pick_line = f"You picked {pick}. " if pick else ""
        await msg.reply_text(
            f"🪙 **{result.capitalize()}**\n\n{pick_line}You won! +{bet:,} WRK$\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )
    else:
        new_bal = await db.update_balance(config.DB_PATH, user.id, -bet)
        pick_line = f"You picked {pick}. " if pick else ""
        await msg.reply_text(
            f"🪙 **{result.capitalize()}**\n\n{pick_line}You lost! -{bet:,} WRK$\n💰 {new_bal:,} WRK$",
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
        new_bal = await db.update_balance(config.DB_PATH, user.id, winnings)
        await msg.reply_text(
            f"🎲 You rolled {player_roll} | Bot rolled {bot_roll}\n\nYou win! +{winnings:,} WRK$\n💰 {new_bal:,} WRK$"
        )
    else:
        new_bal = await db.update_balance(config.DB_PATH, user.id, -bet)
        await msg.reply_text(
            f"🎲 You rolled {player_roll} | Bot rolled {bot_roll}\n\nBot wins. -{bet:,} WRK$\n💰 {new_bal:,} WRK$"
        )
