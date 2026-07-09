import math
import random
import time
import logging
from html import escape

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
    if r < 0.35:
        return round(random.uniform(1.5, 3.0), 2)
    elif r < 0.60:
        return round(random.uniform(3.0, 8.0), 2)
    elif r < 0.78:
        return round(random.uniform(8.0, 25.0), 2)
    elif r < 0.91:
        return round(random.uniform(25.0, 150.0), 2)
    elif r < 0.98:
        return round(random.uniform(150.0, 750.0), 2)
    else:
        return round(random.uniform(750.0, 2500.0), 2)


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


def _resolve_bet(arg: str, balance: int) -> int | None:
    if arg.lower() == "all":
        return balance
    if arg.isdigit():
        return int(arg)
    return None


async def _ensure_wallet(user: object, db_path: str) -> dict:
    await db.upsert_wallet(db_path, user.id, user.username, user.full_name)
    return await db.get_wallet(db_path, user.id)


async def _check_topic(msg) -> bool:
    """Returns True if the message is in the right place. Replies and returns False if not."""
    if msg.chat.type not in ("group", "supergroup"):
        return True
    group = await db.get_group(config.DB_PATH, msg.chat.id)
    if not group:
        return True
    bot_topic_id = group.get("bot_topic_id")
    if not bot_topic_id:
        return True
    if msg.message_thread_id != bot_topic_id:
        await msg.reply_text("⚠️ Economy commands only work in the bot topic.")
        return False
    return True


def topic_gated(func):
    """Decorator: blocks economy commands outside the configured bot topic."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await _check_topic(update.effective_message):
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# ── /balance ──────────────────────────────────────────────────────────────────

@topic_gated
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

@topic_gated
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

    base = random.randint(3000, 8000)
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

    # 25% chance at a gift drop; of those, 1% upgrade to mid-tier
    gift_line = ""
    if await db.is_gifts_seeded(config.DB_PATH) and random.random() < 0.25:
        tier = "mid" if random.random() < 0.01 else "low"
        dropped = await db.get_random_bank_gift(config.DB_PATH, tier)
        if dropped:
            await db.transfer_gift(config.DB_PATH, dropped["id"], user.id)
            from handlers.gifts import _collection_display_name, _bg_emoji, _bg_label, _model_emoji_html
            col_name = escape(_collection_display_name(dropped["collection"]))
            gift_line = (
                f"\n\n🎁 <b>Gift Drop!</b>\n"
                f"{_model_emoji_html(dropped)} {col_name} #{dropped['model_number']} "
                f"{_bg_emoji(dropped['background'])} {escape(_bg_label(dropped['background']))}"
            )

    await update.effective_message.reply_text(
        f"✅ Daily claimed! +{earned:,} WRK${bonus_note}\n"
        f"🔥 Streak: {streak} day(s)\n"
        f"💰 {new_balance:,} WRK${next_milestone}{gift_line}",
        parse_mode="HTML"
    )


# ── /work + /jobs ─────────────────────────────────────────────────────────────
# Tap-to-earn clicker in DMs. Start a shift → tap ⚡ Work repeatedly to
# accumulate earnings → tap 🏁 End Shift to collect. Max 50 taps per shift,
# 15-minute cooldown between shifts. Total lifetime taps unlock promotions.

# (min_taps, title, earn_low, earn_high)  — earn per tap in WRK$
# Tuned so a full 50-tap Intern shift ≈ 4,500 WRK$ avg → low-tier gift in ~2h active play
_JOBS = [
    (0,    "🧑‍🎓 Crypto Intern",     60,   120),
    (100,  "📈 Degen Trader",        120,  250),
    (300,  "🌾 Yield Farmer",        250,  500),
    (600,  "🔍 On-Chain Analyst",    400,  800),
    (1000, "⚙️ Protocol Dev",        600, 1200),
    (2000, "🦈 Blockchain Shark",    900, 1800),
    (5000, "👑 Blockchain Baron",   1500, 3000),
]

_SHIFT_MAX_TAPS = 50
_SHIFT_COOLDOWN = 15 * 60   # 15 min between shifts


def _get_job(tap_count: int) -> tuple:
    job = _JOBS[0]
    for tier in _JOBS:
        if tap_count >= tier[0]:
            job = tier
    return job


def _next_job(tap_count: int) -> tuple | None:
    for tier in _JOBS:
        if tap_count < tier[0]:
            return tier
    return None


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

        if session["taps"] % 5 == 0 and session["taps"] < _SHIFT_MAX_TAPS:
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


async def _end_shift(query, user_id: int, session: dict, auto: bool):
    final = await db.end_work_session(config.DB_PATH, user_id)
    if not final:
        return

    total = final["earned"]
    taps = final["taps"]

    if total == 0:
        await query.edit_message_text("You ended your shift without earning anything. Tap ⚡ next time!")
        return

    new_bal, new_tap_count = await db.claim_work(config.DB_PATH, user_id, total, int(time.time()), taps=taps)

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


async def cmd_jobs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)
    tap_count = wallet.get("work_count", 0) or 0

    lines = ["👔 *Job Board*\n"]
    current_title = _get_job(tap_count)[1]
    for min_taps, title, lo, hi in _JOBS:
        if tap_count >= min_taps:
            marker = "▶ " if title == current_title else "✅ "
        else:
            marker = f"🔒 {min_taps} taps — "
        lines.append(f"{marker}{title}  ({lo}–{hi} WRK$/tap)")

    lines.append(f"\n👆 Your lifetime taps: {tap_count}")
    next_tier = _next_job(tap_count)
    if next_tier:
        lines.append(f"📊 {next_tier[0] - tap_count} more taps to unlock {next_tier[1]}")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /leaderboard ──────────────────────────────────────────────────────────────

@topic_gated
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
        username = row.get("username")
        if username:
            display = f"[{name}](https://t.me/{username.lstrip('@')})"
        else:
            display = name
        lines.append(f"{prefix} {display} — {row['balance']:,} WRK$")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /workreminder ─────────────────────────────────────────────────────────────

async def cmd_workreminder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await _ensure_wallet(user, config.DB_PATH)
    new_state = await db.toggle_work_reminder(config.DB_PATH, user.id)
    if new_state:
        await update.effective_message.reply_text(
            "🔔 Work reminder *ON* — I'll DM you when your shift is ready!",
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text("🔕 Work reminder *OFF*", parse_mode="Markdown")


# ── Economy admin (owner only) ────────────────────────────────────────────────

async def cmd_givewrk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != config.OWNER_ID:
        return

    if len(ctx.args) < 2 or not ctx.args[1].lstrip("-").isdigit():
        await msg.reply_text("Usage: `/givewrk @username <amount>`", parse_mode="Markdown")
        return

    target_row = await db.get_user_by_username(config.DB_PATH, msg.chat.id, ctx.args[0])
    if not target_row:
        await msg.reply_text("❌ User not found in this chat's activity log.")
        return

    amount = int(ctx.args[1])
    await db.upsert_wallet(config.DB_PATH, target_row["user_id"], None, None)
    new_bal = await db.update_balance(config.DB_PATH, target_row["user_id"], amount)
    name = target_row.get("full_name") or ctx.args[0]
    action = f"+{amount:,}" if amount >= 0 else f"{amount:,}"
    await msg.reply_text(f"✅ {action} WRK$ → {name}\n💰 New balance: {new_bal:,} WRK$")


async def cmd_setwrk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != config.OWNER_ID:
        return

    if len(ctx.args) < 2 or not ctx.args[1].isdigit():
        await msg.reply_text("Usage: `/setwrk @username <amount>`", parse_mode="Markdown")
        return

    target_row = await db.get_user_by_username(config.DB_PATH, msg.chat.id, ctx.args[0])
    if not target_row:
        await msg.reply_text("❌ User not found in this chat's activity log.")
        return

    target_id = target_row["user_id"]
    await db.upsert_wallet(config.DB_PATH, target_id, None, None)
    wallet = await db.get_wallet(config.DB_PATH, target_id)
    new_amount = int(ctx.args[1])
    delta = new_amount - wallet["balance"]
    new_bal = await db.update_balance(config.DB_PATH, target_id, delta)
    name = target_row.get("full_name") or ctx.args[0]
    await msg.reply_text(f"✅ Set {name}'s balance to {new_bal:,} WRK$")


# ── /give ─────────────────────────────────────────────────────────────────────

@topic_gated
async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    if len(ctx.args) < 2 or not ctx.args[1].isdigit():
        await msg.reply_text("Usage: `/give @username <amount>`", parse_mode="Markdown")
        return

    amount = int(ctx.args[1])
    if amount <= 0:
        await msg.reply_text("❌ Amount must be positive.")
        return
    if wallet["balance"] < amount:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

    target_row = await db.get_user_by_username(config.DB_PATH, msg.chat.id, ctx.args[0])
    if not target_row:
        await msg.reply_text("❌ Can't find that user. They need to have sent a message first.")
        return

    target_id = target_row["user_id"]
    target_name = target_row.get("full_name") or ctx.args[0]

    if target_id == user.id:
        await msg.reply_text("❌ You can't give money to yourself.")
        return

    await db.upsert_wallet(config.DB_PATH, target_id, None, None)
    new_sender_bal = await db.update_balance(config.DB_PATH, user.id, -amount)
    await db.update_balance(config.DB_PATH, target_id, amount)

    await msg.reply_text(
        f"💸 {display_name(user)} gave {target_name} {amount:,} WRK$!\n"
        f"💰 Your balance: {new_sender_bal:,} WRK$"
    )


# ── /hack ─────────────────────────────────────────────────────────────────────

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

_hack_cooldowns: dict[int, float] = {}   # user_id -> timestamp
_hack_games: dict[int, dict] = {}        # user_id -> active game state
_HACK_GAME_TTL = 3600  # abandon after 1 hour


def _hack_display(word: str, revealed: set[int]) -> str:
    return " ".join(c if i in revealed else "_" for i, c in enumerate(word))


@topic_gated
async def cmd_hack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    await _ensure_wallet(user, config.DB_PATH)

    now = time.time()
    last = _hack_cooldowns.get(user.id, 0)
    if now - last < 3600:
        remaining = int(3600 - (now - last))
        m, s = divmod(remaining, 60)
        await msg.reply_text(f"⏳ Hack cooldown: {m}m {s}s remaining.")
        return

    if user.id in _hack_games:
        game = _hack_games[user.id]
        # Auto-expire abandoned games older than 1 hour
        if now - game.get("started_at", 0) > _HACK_GAME_TTL:
            del _hack_games[user.id]
            _hack_cooldowns[user.id] = now  # still apply cooldown
        else:
            display = _hack_display(game["word"], game["revealed"])
            await msg.reply_text(
                f"🖥️ You already have an active hack session!\n\n"
                f"`{display}`\n_{game['clue']}_\n\n"
                f"Attempts left: {game['attempts']}\nUse `/guess <word>` to answer.",
                parse_mode="Markdown"
            )
            return

    word, clue = random.choice(_WORDLIST)
    reward = random.randint(5000, 15000)
    revealed = {0}  # always reveal first letter

    _hack_games[user.id] = {
        "word": word,
        "clue": clue,
        "reward": reward,
        "attempts": 5,
        "revealed": revealed,
        "started_at": now,
    }

    display = _hack_display(word, revealed)
    await msg.reply_text(
        f"🖥️ *Hacking a wallet...*\n\n"
        f"Clue: _{clue}_\n\n"
        f"`{display}` ({len(word)} letters)\n\n"
        f"You have 5 attempts. Use `/guess <word>` to crack it.\n"
        f"💰 Reward: {reward:,} WRK$",
        parse_mode="Markdown"
    )


@topic_gated
async def cmd_guess(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    game = _hack_games.get(user.id)
    if not game:
        await msg.reply_text("❌ No active hack session. Start one with `/hack`.", parse_mode="Markdown")
        return

    if not ctx.args:
        await msg.reply_text("Usage: `/guess <word>`", parse_mode="Markdown")
        return

    guess = " ".join(ctx.args).lower().strip()
    word = game["word"]

    if guess == word:
        del _hack_games[user.id]
        _hack_cooldowns[user.id] = time.time()
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

    game["attempts"] -= 1

    if game["attempts"] <= 0:
        del _hack_games[user.id]
        _hack_cooldowns[user.id] = time.time()
        await msg.reply_text(
            f"❌ *CONNECTION TERMINATED*\n\n"
            f"The word was `{word}`.\n"
            f"You got traced. Better luck next time.",
            parse_mode="Markdown"
        )
        return

    # Reveal another letter on wrong guess
    unrevealed = [i for i in range(len(word)) if i not in game["revealed"]]
    if unrevealed:
        game["revealed"].add(random.choice(unrevealed))

    display = _hack_display(word, game["revealed"])
    await msg.reply_text(
        f"❌ Wrong. {game['attempts']} attempt(s) left.\n\n"
        f"`{display}`\n_{game['clue']}_",
        parse_mode="Markdown"
    )


# ── /rob ──────────────────────────────────────────────────────────────────────

_rob_cooldowns: dict[int, float] = {}  # user_id -> timestamp

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

@topic_gated
async def cmd_rob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    robber = update.effective_user
    robber_wallet = await _ensure_wallet(robber, config.DB_PATH)

    now = time.time()
    last_rob = _rob_cooldowns.get(robber.id, 0)
    if now - last_rob < 900:
        remaining = int(900 - (now - last_rob))
        m, s = divmod(remaining, 60)
        await msg.reply_text(f"⏳ Rob cooldown: {m}m {s}s remaining.")
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

    robber_name = display_name(robber)

    if result["outcome"] == "success":
        amount = result["amount"]
        await db.update_balance(config.DB_PATH, target_id, -amount)
        new_bal = await db.update_balance(config.DB_PATH, robber.id, amount)
        emoji, template = random.choice(_ROB_SUCCESS)
        line = template.format(robber=robber_name, target=target_name, amount=f"{amount:,}")
        await msg.reply_text(f"{emoji} {line}\n💰 Your balance: {new_bal:,} WRK$")
    elif result["outcome"] == "fine":
        amount = result["amount"]
        new_bal = await db.update_balance(config.DB_PATH, robber.id, -amount)
        emoji, template = random.choice(_ROB_FINE)
        line = template.format(robber=robber_name, target=target_name, amount=f"{amount:,}")
        await msg.reply_text(f"{emoji} {line}\n💰 Your balance: {new_bal:,} WRK$")
    elif result["outcome"] == "bail":
        amount = result["amount"]
        new_bal = await db.update_balance(config.DB_PATH, robber.id, -amount)
        emoji, template = random.choice(_ROB_BAIL)
        line = template.format(robber=robber_name, target=target_name, amount=f"{amount:,}")
        await msg.reply_text(f"{emoji} {line}\n💰 Your balance: {new_bal:,} WRK$")
    else:  # getaway
        emoji, template = random.choice(_ROB_GETAWAY)
        line = template.format(robber=robber_name, target=target_name, amount="0")
        await msg.reply_text(f"{emoji} {line}\n💰 Your balance: {robber_wallet['balance']:,} WRK$")


# ── /slots ────────────────────────────────────────────────────────────────────

@topic_gated
async def cmd_slots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    bet = _resolve_bet(ctx.args[0], wallet["balance"]) if ctx.args else None
    if bet is None:
        await msg.reply_text("Usage: `/slots <bet|all>`", parse_mode="Markdown")
        return
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

@topic_gated
async def cmd_coinflip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    bet = _resolve_bet(ctx.args[0], wallet["balance"]) if ctx.args else None
    if bet is None:
        await msg.reply_text("Usage: `/coinflip <bet|all> [heads|tails]`", parse_mode="Markdown")
        return
    if bet < 10:
        await msg.reply_text("❌ Minimum bet is 10 WRK$.")
        return
    if wallet["balance"] < bet:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

    pick = None
    if len(ctx.args) > 1:
        pick = ctx.args[1].lower()
        if pick not in ("heads", "tails"):
            await msg.reply_text("❌ Invalid pick — use `heads` or `tails`.", parse_mode="Markdown")
            return

    result = random.choice(["heads", "tails"])
    won = (pick == result) if pick else (random.random() < 0.50)

    pick_line = f"You picked {pick}. " if pick else ""
    if won:
        new_bal = await db.update_balance(config.DB_PATH, user.id, bet)
        await msg.reply_text(
            f"🪙 **{result.capitalize()}**\n\n{pick_line}You won! +{bet:,} WRK$\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )
    else:
        new_bal = await db.update_balance(config.DB_PATH, user.id, -bet)
        await msg.reply_text(
            f"🪙 **{result.capitalize()}**\n\n{pick_line}You lost! -{bet:,} WRK$\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )


# ── /dice ─────────────────────────────────────────────────────────────────────

@topic_gated
async def cmd_dice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    bet = _resolve_bet(ctx.args[0], wallet["balance"]) if ctx.args else None
    if bet is None:
        await msg.reply_text("Usage: `/dice <bet|all>`", parse_mode="Markdown")
        return
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


# ── /blackjack ────────────────────────────────────────────────────────────────

def _bj_card_points(rank: str) -> int:
    if rank in ('J', 'Q', 'K'):
        return 10
    if rank == 'A':
        return 11
    return int(rank)


def _bj_render(hands: list, dealer_hand: list, current_hand: int = 0, hide_dealer: bool = True) -> str:
    def fmt_hand(hand):
        return " ".join(f"{r}{s}" for r, s in hand)
    if hide_dealer:
        visible_value = _bj_hand_value([dealer_hand[0]])
        dealer_display = f"{dealer_hand[0][0]}{dealer_hand[0][1]} ?? = **{visible_value}+?**"
    else:
        dealer_display = f"{fmt_hand(dealer_hand)} = **{_bj_hand_value(dealer_hand)}**"
    lines = ["🃏 *Blackjack*\n"]
    if len(hands) == 1:
        lines.append(f"Your hand: {fmt_hand(hands[0])} = **{_bj_hand_value(hands[0])}**")
    else:
        for i, hand in enumerate(hands):
            marker = "▶ " if i == current_hand else "   "
            lines.append(f"{marker}Hand {i+1}: {fmt_hand(hand)} = **{_bj_hand_value(hand)}**")
    lines.append(f"Dealer: {dealer_display}")
    return "\n".join(lines)


def _bj_keyboard(user_id: int, can_double: bool = False, can_split: bool = False) -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton("👊 Hit", callback_data=f"bj:hit:{user_id}"),
        InlineKeyboardButton("✋ Stand", callback_data=f"bj:stand:{user_id}"),
    ]
    row2 = []
    if can_double:
        row2.append(InlineKeyboardButton("2️⃣ Double", callback_data=f"bj:double:{user_id}"))
    if can_split:
        row2.append(InlineKeyboardButton("✂️ Split", callback_data=f"bj:split:{user_id}"))
    rows = [row1, row2] if row2 else [row1]
    return InlineKeyboardMarkup(rows)


def _bj_active_keyboard(game: dict, user_id: int, balance: int) -> InlineKeyboardMarkup:
    hand = game["hands"][game["current_hand"]]
    is_first_action = len(hand) == 2
    can_double = is_first_action and balance >= game["bet"]
    can_split = (
        is_first_action
        and len(game["hands"]) == 1
        and _bj_card_points(hand[0][0]) == _bj_card_points(hand[1][0])
        and balance >= game["bet"]
    )
    return _bj_keyboard(user_id, can_double=can_double, can_split=can_split)


async def _bj_resolve(query, game: dict, user_id: int, wallet: dict):
    del _bj_games[user_id]
    dealer_hand = game["dealer"]
    deck = game["deck"]

    # Dealer plays out only if at least one player hand hasn't busted
    if any(_bj_hand_value(h) <= 21 for h in game["hands"]):
        while _bj_hand_value(dealer_hand) < 17:
            dealer_hand.append(deck.pop())
    dealer_val = _bj_hand_value(dealer_hand)

    total_delta = 0
    result_lines = []
    for i, hand in enumerate(game["hands"]):
        hand_bet = game["bet"] * (2 if game["doubled"][i] else 1)
        player_val = _bj_hand_value(hand)
        if player_val > 21:
            result_lines.append(f"Hand {i+1}: 💥 Bust  −{hand_bet:,}")
            total_delta -= hand_bet
        elif dealer_val > 21 or player_val > dealer_val:
            result_lines.append(f"Hand {i+1}: 🏆 Win  +{hand_bet:,}")
            total_delta += hand_bet
        elif player_val == dealer_val:
            result_lines.append(f"Hand {i+1}: 🤝 Push")
        else:
            result_lines.append(f"Hand {i+1}: 😞 Lose  −{hand_bet:,}")
            total_delta -= hand_bet

    if len(game["hands"]) == 1:
        result_lines = [result_lines[0].replace("Hand 1: ", "")]

    new_bal = await db.update_balance(config.DB_PATH, user_id, total_delta)
    result_text = "\n".join(result_lines)
    if len(game["hands"]) > 1:
        sign = "+" if total_delta > 0 else ""
        result_text += f"\n\nNet: {sign}{total_delta:,} WRK$"

    await query.edit_message_text(
        f"{_bj_render(game['hands'], dealer_hand, hide_dealer=False)}\n\n"
        f"{result_text}\n💰 {new_bal:,} WRK$",
        parse_mode="Markdown"
    )


@topic_gated
async def cmd_blackjack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    wallet = await _ensure_wallet(user, config.DB_PATH)

    if user.id in _bj_games:
        await msg.reply_text("❌ You already have an active blackjack game. Finish it first.")
        return
    bet = _resolve_bet(ctx.args[0], wallet["balance"]) if ctx.args else None
    if bet is None:
        await msg.reply_text("Usage: `/blackjack <bet|all>`", parse_mode="Markdown")
        return
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
        "hands": [player],
        "current_hand": 0,
        "doubled": [False],
        "dealer": dealer,
        "chat_id": msg.chat.id,
    }

    if _bj_is_blackjack(player):
        del _bj_games[user.id]
        winnings = int(bet * 1.5)
        new_bal = await db.update_balance(config.DB_PATH, user.id, winnings)
        await msg.reply_text(
            f"{_bj_render([player], dealer, hide_dealer=False)}\n\n"
            f"🎉 Blackjack! +{winnings:,} WRK$\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
        )
        return

    keyboard = _bj_active_keyboard(_bj_games[user.id], user.id, wallet["balance"])
    sent = await msg.reply_text(
        _bj_render([player], dealer),
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    _bj_games[user.id]["message_id"] = sent.message_id


async def blackjack_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, action, uid_str = query.data.split(":")
    user_id = int(uid_str)

    if query.from_user.id != user_id:
        await query.answer("This isn't your game.", show_alert=True)
        return

    await query.answer()

    game = _bj_games.get(user_id)
    if not game:
        await query.edit_message_text("Game expired.")
        return

    wallet = await db.get_wallet(config.DB_PATH, user_id)
    ci = game["current_hand"]
    hand = game["hands"][ci]

    if action == "hit":
        hand.append(game["deck"].pop())
        if _bj_hand_value(hand) > 21:
            if ci < len(game["hands"]) - 1:
                game["current_hand"] += 1
                kb = _bj_active_keyboard(game, user_id, wallet["balance"])
                await query.edit_message_text(
                    f"{_bj_render(game['hands'], game['dealer'], game['current_hand'])}\n\n💥 Hand {ci+1} bust!",
                    parse_mode="Markdown", reply_markup=kb
                )
            else:
                await _bj_resolve(query, game, user_id, wallet)
        else:
            kb = _bj_active_keyboard(game, user_id, wallet["balance"])
            await query.edit_message_text(
                _bj_render(game["hands"], game["dealer"], ci),
                parse_mode="Markdown", reply_markup=kb
            )

    elif action == "stand":
        if ci < len(game["hands"]) - 1:
            game["current_hand"] += 1
            kb = _bj_active_keyboard(game, user_id, wallet["balance"])
            await query.edit_message_text(
                _bj_render(game["hands"], game["dealer"], game["current_hand"]),
                parse_mode="Markdown", reply_markup=kb
            )
        else:
            await _bj_resolve(query, game, user_id, wallet)

    elif action == "double":
        if len(hand) != 2 or wallet["balance"] < game["bet"]:
            await query.answer("Can't double now.", show_alert=True)
            return
        game["doubled"][ci] = True
        hand.append(game["deck"].pop())
        if ci < len(game["hands"]) - 1:
            game["current_hand"] += 1
            kb = _bj_active_keyboard(game, user_id, wallet["balance"])
            await query.edit_message_text(
                _bj_render(game["hands"], game["dealer"], game["current_hand"]),
                parse_mode="Markdown", reply_markup=kb
            )
        else:
            await _bj_resolve(query, game, user_id, wallet)

    elif action == "split":
        if len(hand) != 2 or len(game["hands"]) > 1 or wallet["balance"] < game["bet"]:
            await query.answer("Can't split now.", show_alert=True)
            return
        card1, card2 = hand
        game["hands"] = [[card1, game["deck"].pop()], [card2, game["deck"].pop()]]
        game["doubled"] = [False, False]
        game["current_hand"] = 0
        kb = _bj_active_keyboard(game, user_id, wallet["balance"])
        await query.edit_message_text(
            _bj_render(game["hands"], game["dealer"], 0),
            parse_mode="Markdown", reply_markup=kb
        )


# ── /crash ────────────────────────────────────────────────────────────────────

@topic_gated
async def cmd_crash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat_id = msg.chat.id
    wallet = await _ensure_wallet(user, config.DB_PATH)

    bet = _resolve_bet(ctx.args[0], wallet["balance"]) if ctx.args else None
    if bet is None:
        await msg.reply_text("Usage: `/crash <bet|all>`", parse_mode="Markdown")
        return
    if bet < 10:
        await msg.reply_text("❌ Minimum bet is 10 WRK$.")
        return
    if wallet["balance"] < bet:
        await msg.reply_text(f"❌ Not enough WRK$. Your balance: {wallet['balance']:,}")
        return

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

    thread_id = msg.message_thread_id
    crash_point = _generate_crash_point()
    _crash_games[chat_id] = {
        "state": "joining",
        "crash_point": crash_point,
        "ticks": 0,
        "thread_id": thread_id,
        "players": {
            user.id: {"bet": bet, "name": display_name(user), "cashed_out": False, "cash_out_mult": None}
        },
        "announcement_id": None,
        "live_msg_id": None,
    }
    await db.update_balance(config.DB_PATH, user.id, -bet)

    sent = await msg.reply_text(
        f"🚀 <b>{escape(display_name(user))} started Crash!</b>\n"
        f"Type /crash &lt;bet&gt; to join.\n\n"
        f"Starting in 10...",
        parse_mode="HTML"
    )
    _crash_games[chat_id]["announcement_id"] = sent.message_id

    ctx.application.job_queue.run_repeating(
        _crash_countdown_tick,
        interval=1,
        first=1,
        data={"chat_id": chat_id, "tick": 0, "announcement_id": sent.message_id, "thread_id": thread_id},
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
                    f"🚀 <b>Crash starting soon!</b>\n"
                    f"Type /crash &lt;bet&gt; to join.\n\n"
                    f"Starting in {remaining}..."
                ),
                parse_mode="HTML"
            )
        except TelegramError:
            pass
        return

    ctx.job.schedule_removal()
    game["state"] = "running"

    player_list = "\n".join(
        f"  • {escape(p['name'])} ({p['bet']:,} WRK$)" for p in game["players"].values()
    )
    sent = await ctx.bot.send_message(
        chat_id=chat_id,
        message_thread_id=game.get("thread_id"),
        text=f"🚀 <b>CRASH IS LIVE!</b>\n\nMultiplier: <b>1.00x</b>\n\nPlayers:\n{player_list}\n\nType /cashout to lock in!",
        parse_mode="HTML"
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

    active_lines = "\n".join(f"  • {escape(p['name'])} ({p['bet']:,} WRK$)" for p in active)
    cashed_lines = "\n".join(
        f"  ✅ {escape(p['name'])} cashed @ {p['cash_out_mult']}x"
        for p in game["players"].values() if p["cashed_out"]
    )
    body = f"🚀 <b>CRASH LIVE — {mult}x</b>\n\nIn:\n{active_lines}"
    if cashed_lines:
        body += f"\n\nCashed out:\n{cashed_lines}"
    body += "\n\nType /cashout to lock in!"

    try:
        await ctx.bot.edit_message_text(
            chat_id=chat_id,
            message_id=game["live_msg_id"],
            text=body,
            parse_mode="HTML"
        )
    except TelegramError as e:
        log.warning("crash tick edit failed chat=%s: %s", chat_id, e)


@topic_gated
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

    new_bal = await db.update_balance(config.DB_PATH, user.id, winnings)
    await msg.reply_text(
        f"💰 Cashed out @ {mult}x! +{winnings:,} WRK$\n"
        f"Balance: {new_bal:,} WRK$"
    )


async def _crash_end(bot, chat_id: int, game: dict, crashed_at: float):
    game["state"] = "crashed"
    lines = [f"💥 <b>CRASHED @ {crashed_at:.2f}x</b>\n"]
    for uid, p in game["players"].items():
        if p["cashed_out"]:
            profit = int(p["bet"] * p["cash_out_mult"]) - p["bet"]
            lines.append(f"✅ {escape(p['name'])} — cashed @ {p['cash_out_mult']}x (+{profit:,} WRK$)")
        else:
            lines.append(f"💀 {escape(p['name'])} — lost {p['bet']:,} WRK$")

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=game["live_msg_id"],
            text="\n".join(lines),
            parse_mode="HTML"
        )
    except TelegramError:
        await bot.send_message(chat_id=chat_id, message_thread_id=game.get("thread_id"), text="\n".join(lines), parse_mode="HTML")

    del _crash_games[chat_id]
