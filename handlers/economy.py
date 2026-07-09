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
        mention = f"[{name}](tg://user?id={row['user_id']})"
        lines.append(f"{prefix} {mention} — {row['balance']:,} WRK$")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


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
    ("seed",        "The 12-24 word key to your entire crypto kingdom."),
    ("phrase",      "What you never screenshot. What you never share. Starts with 'ph'."),
    ("wallet",      "Where your bags live on-chain."),
    ("hodl",        "A typo that became a crypto religion."),
    ("whale",       "Someone who moves markets just by breathing."),
    ("mempool",     "The waiting room for unconfirmed transactions."),
    ("airdrop",     "Free tokens that land in your wallet out of nowhere."),
    ("rugpull",     "The dev said 'we're not going anywhere.' Then they went anywhere."),
    ("halving",     "When Bitcoin's block reward gets cut in half."),
    ("staking",     "Locking up tokens to earn passive income."),
    ("validator",   "Keeps the network honest. Gets paid to do it."),
    ("nominator",   "Backs a validator with their stake on TON."),
    ("jetton",      "TON's version of a token standard."),
    ("workchain",   "A parallel chain in the TON architecture."),
    ("genesis",     "The very first block of a blockchain."),
    ("liquidity",   "The lifeblood of every DEX pool."),
    ("tokenomics",  "The economics behind a token's supply and distribution."),
    ("whitepaper",  "The document where every project promises to change the world."),
    ("defi",        "Banking, but the bank is a smart contract."),
    ("mainnet",     "The real network, not the playground."),
    ("testnet",     "Where devs break things before breaking the real thing."),
    ("hash",        "A fixed-length fingerprint of data."),
    ("fork",        "When a blockchain splits and takes a different path."),
    ("dao",         "A community that governs itself through votes and tokens."),
    ("degen",       "Someone who apes into anything with triple-digit APY."),
    ("moonshot",    "A bet so wild it either 100xs or goes to zero."),
    ("rekt",        "When the trade goes the wrong way. Very wrong."),
    ("shill",       "Promoting a token you hold and hope others buy."),
    ("gas",         "The fee that powers every Ethereum transaction."),
    ("altcoin",     "Any crypto that isn't Bitcoin."),
    ("bullish",     "Confident the price is going up."),
    ("bearish",     "Expecting the price to go down."),
    ("pump",        "When a coin's price spikes fast, usually suspiciously."),
    ("dump",        "What happens right after the pump."),
    ("nft",         "A unique digital asset on-chain. Three letters."),
    ("gram",        "TON's rebranded ticker. The coin in this very bot."),
    ("node",        "A computer that participates in a blockchain network."),
    ("block",       "A bundle of transactions added to the chain."),
    ("miner",       "Solves puzzles to add blocks and earn rewards."),
    ("ledger",      "A record of all transactions. Also a cold wallet brand."),
    ("satoshi",     "The smallest unit of Bitcoin. Also Bitcoin's creator."),
    ("ethereum",    "Home of smart contracts. Gas fees hit different here."),
    ("bitcoin",     "The original. The OG. The one that started it all."),
    ("sniper",      "A bot that buys a token the millisecond it launches."),
    ("bridge",      "Moves assets from one chain to another."),
    ("coldwallet",  "A hardware device that keeps your keys offline."),
    ("multisig",    "Requires multiple keys to sign a transaction."),
    ("slippage",    "The difference between the price you expect and what you get."),
]

_hack_cooldowns: dict[int, float] = {}   # user_id -> timestamp
_hack_games: dict[int, dict] = {}        # user_id -> active game state


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
        display = _hack_display(game["word"], game["revealed"])
        await msg.reply_text(
            f"🖥️ You already have an active hack session!\n\n"
            f"`{display}`\n_{game['clue']}_\n\n"
            f"Attempts left: {game['attempts']}\nUse `/guess <word>` to answer.",
            parse_mode="Markdown"
        )
        return

    word, clue = random.choice(_WORDLIST)
    reward = random.randint(800, 2500)
    revealed = {0}  # always reveal first letter

    _hack_games[user.id] = {
        "word": word,
        "clue": clue,
        "reward": reward,
        "attempts": 3,
        "revealed": revealed,
    }

    display = _hack_display(word, revealed)
    await msg.reply_text(
        f"🖥️ *Hacking a wallet...*\n\n"
        f"Clue: _{clue}_\n\n"
        f"`{display}` ({len(word)} letters)\n\n"
        f"You have 3 attempts. Use `/guess <word>` to crack it.\n"
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

def _bj_render(player_hand, dealer_hand, hide_dealer=True) -> str:
    def fmt_hand(hand):
        return " ".join(f"{r}{s}" for r, s in hand)
    if hide_dealer:
        visible_value = _bj_hand_value([dealer_hand[0]])
        dealer_display = f"{dealer_hand[0][0]}{dealer_hand[0][1]} ?? = **{visible_value}+?**"
    else:
        dealer_display = f"{fmt_hand(dealer_hand)} = **{_bj_hand_value(dealer_hand)}**"
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
        "player": player,
        "dealer": dealer,
        "chat_id": msg.chat.id,
    }

    if _bj_is_blackjack(player):
        del _bj_games[user.id]
        winnings = int(bet * 1.5)
        new_bal = await db.update_balance(config.DB_PATH, user.id, winnings)
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
    bet = game["bet"]

    if action == "hit":
        game["player"].append(game["deck"].pop())
        val = _bj_hand_value(game["player"])
        if val > 21:
            del _bj_games[user_id]
            new_bal = await db.update_balance(config.DB_PATH, user_id, -bet)
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
            new_bal = await db.update_balance(config.DB_PATH, user_id, bet)
            result = f"🏆 You win! +{bet:,} WRK$"
        elif player_val == dealer_val:
            result = f"🤝 Push — bet returned."
            new_bal = wallet["balance"]
        else:
            new_bal = await db.update_balance(config.DB_PATH, user_id, -bet)
            result = f"😞 Dealer wins. -{bet:,} WRK$"

        await query.edit_message_text(
            f"{_bj_render(game['player'], dealer_hand, hide_dealer=False)}\n\n"
            f"{result}\n💰 {new_bal:,} WRK$",
            parse_mode="Markdown"
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
                    f"🚀 *Crash starting soon!*\n"
                    f"Type `/crash <bet>` to join.\n\n"
                    f"Starting in {remaining}..."
                ),
                parse_mode="Markdown"
            )
        except TelegramError:
            pass
        return

    ctx.job.schedule_removal()
    game["state"] = "running"

    player_list = "\n".join(
        f"  • {p['name']} ({p['bet']:,} WRK$)" for p in game["players"].values()
    )
    sent = await ctx.bot.send_message(
        chat_id=chat_id,
        message_thread_id=game.get("thread_id"),
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
        await bot.send_message(chat_id=chat_id, message_thread_id=game.get("thread_id"), text="\n".join(lines), parse_mode="Markdown")

    del _crash_games[chat_id]
