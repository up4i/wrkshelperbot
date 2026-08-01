import asyncio
from decimal import Decimal
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from command_catalog import build_help_text
from simulated_market import get_market_snapshot


def _entry_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Open WRK$ Mini App", url=config.MINI_APP_URL)],
        [InlineKeyboardButton(
            "➕ Add WRK$ to a group",
            url=f"https://t.me/{bot_username}?startgroup=true",
        )],
    ])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 Welcome to WRK$. Earn, play, collect, trade, and compete with other "
        "Telegram players.",
        reply_markup=_entry_keyboard(ctx.bot.username),
    )


async def cmd_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Your wallet, games, market, trades, jobs, and profile are in the WRK$ mini app.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💎 Open WRK$ Mini App", url=config.MINI_APP_URL)
        ]]),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        build_help_text(),
        parse_mode="Markdown",
        reply_markup=_entry_keyboard(ctx.bot.username),
    )


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    wallet_url = f"{config.MINI_APP_URL.split('?', 1)[0]}?startapp=wallet"
    await update.effective_message.reply_text(
        "Open Wallet to manage WRK$, simulated GRAM and memecoins, custom game "
        "addresses, and collectibles.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👛 Open Wallet", url=wallet_url)
        ]]),
    )


def _market_price(value: str) -> str:
    price = Decimal(value)
    if price >= 1:
        return f"${price:,.4f}"
    if price >= Decimal("0.01"):
        return f"${price:,.6f}"
    return f"${price:,.8f}".rstrip("0")


async def cmd_coins(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        snapshot = await asyncio.to_thread(get_market_snapshot, config.DB_PATH)
    except Exception:
        await update.effective_message.reply_text(
            "The simulated coin market is temporarily unavailable. Please try again shortly."
        )
        return
    lines = [
        "🪙 <b>STONK.fi simulated market</b>",
        "<i>User trades move pool prices, liquidity, volume, and market caps</i>",
        "",
    ]
    for token in snapshot["tokens"]:
        stale = " · stale" if token["stale"] else ""
        gram_quote = ""
        if token["symbol"] != "GRAM" and token.get("price_gram"):
            gram_quote = f" · {Decimal(token['price_gram']):.8f} GRAM"
        change = Decimal(token.get("price_change_24h", "0"))
        lines.append(
            f"<b>${escape(token['symbol'])}</b> {_market_price(token['price_usd'])}"
            f" {change:+.2f}%{gram_quote}{stale}\n"
            f"MC ${Decimal(token['market_cap_usd']):,.0f} · "
            f"Liq {Decimal(token['liquidity_gram']):,.0f} GRAM"
        )
    lines.append(f"\nPool fee: {snapshot['fee_bps'] / 100:.2f}% per swap leg")
    coins_url = f"{config.MINI_APP_URL.split('?', 1)[0]}?startapp=coins"
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Open coin shop", url=coins_url)
        ]]),
    )


async def on_group_activity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Track economy players so reply and @username targeting works in groups."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or user.is_bot or not chat:
        return
    await db.update_activity(
        config.DB_PATH,
        chat.id,
        user.id,
        user.username,
        user.full_name,
    )


async def on_bot_added(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.new_chat_members:
        return
    if not any(user.id == ctx.bot.id for user in msg.new_chat_members):
        return
    try:
        await ctx.bot.send_message(
            msg.chat.id,
            "🎮 WRK$ is live here.\n\n"
            "Use /flex to show your profile. Reply to a player with /rob or /hack "
            "to challenge them. Use /help for the full economy command list.\n\n"
            "All WRK$, GRAM, memecoins, raids, and wallet addresses are fictional game assets.",
            reply_markup=_entry_keyboard(ctx.bot.username),
        )
    except TelegramError:
        pass
