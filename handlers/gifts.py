import random
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import display_name, is_admin

log = logging.getLogger(__name__)

_BG_EMOJIS = {
    "black": "⬛", "onyx": "🖤", "grape": "🟣",
    "emerald": "🟢", "midnight": "🔵", "orange": "🟠",
}
_BG_LABELS = {
    "black": "Black", "onyx": "Onyx Black", "grape": "Grape",
    "emerald": "Emerald", "midnight": "Midnight Blue", "orange": "Orange",
}
_BG_MULTIPLIERS = {
    "black": 3.0, "onyx": 2.5, "grape": 2.0,
    "emerald": 1.5, "midnight": 1.2, "orange": 1.0,
}
_BACKGROUNDS = ["black", "onyx", "grape", "emerald", "midnight", "orange"]
_GIFTS_PER_PAGE = 5


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _bg_emoji(bg: str) -> str:
    return _BG_EMOJIS.get(bg, "🎁")

def _bg_label(bg: str) -> str:
    return _BG_LABELS.get(bg, bg.title())

def _tier_label(tier: str) -> str:
    return {"low": "⚪ Common", "mid": "🔵 Rare", "high": "🟡 Legendary"}.get(tier, tier)

def _collection_display_name(key: str) -> str:
    return " ".join(w.capitalize() for w in key.split("_"))

def _price_floor(base_price: int) -> int:
    return int(base_price * 0.40)

def _price_ceiling(base_price: int) -> int:
    return int(base_price * 5.0)

def _format_gift_card(instance: dict, current_price: int) -> str:
    col_name = _collection_display_name(instance["collection"])
    num = instance["model_number"]
    bg_emoji = _bg_emoji(instance["background"])
    bg_label = _bg_label(instance["background"])
    bg_mult = _BG_MULTIPLIERS.get(instance["background"], 1.0)
    return (
        f"{instance['model_emoji']} *{col_name} #{num}*\n\n"
        f"Model: {instance['model_emoji']} {instance['model_name']} · {instance['model_rarity_pct']}%\n"
        f"Background: {bg_emoji} {bg_label} · {bg_mult}x\n"
        f"Rarity: {_tier_label(instance['tier'])}\n\n"
        f"💰 Current value: {current_price:,} WRK$"
    )


# ── /seedgifts (owner only) ───────────────────────────────────────────────────

async def cmd_seedgifts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != config.OWNER_ID:
        return

    from data.gift_catalog import CATALOG
    await msg.reply_text("⏳ Seeding gift catalog... this may take a moment.")
    await db.seed_gifts(config.DB_PATH, CATALOG)
    total = sum(len(c["models"]) * 6 for c in CATALOG.values())
    await msg.reply_text(f"✅ Gift catalog seeded.\n{len(CATALOG)} collections · {total:,} unique instances")


# ── /inv ─────────────────────────────────────────────────────────────────────

def _inv_keyboard(gifts: list[dict], page: int, user_id: int) -> InlineKeyboardMarkup:
    start = page * _GIFTS_PER_PAGE
    page_gifts = gifts[start:start + _GIFTS_PER_PAGE]
    rows = []
    for g in page_gifts:
        col_name = _collection_display_name(g["collection"])
        label = f"{g['model_emoji']} {col_name} #{g['model_number']} {_bg_emoji(g['background'])}"
        rows.append([InlineKeyboardButton(label, callback_data=f"gifts:detail:{user_id}:{g['id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"gifts:page:{user_id}:{page - 1}"))
    total_pages = (len(gifts) + _GIFTS_PER_PAGE - 1) // _GIFTS_PER_PAGE
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"gifts:page:{user_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def cmd_inventory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    gifts = await db.get_user_gifts(config.DB_PATH, user.id)
    if not gifts:
        await msg.reply_text("🎁 Your gift inventory is empty. Try `/daily` or `/shop`!", parse_mode="Markdown")
        return
    kb = _inv_keyboard(gifts, page=0, user_id=user.id)
    total_pages = (len(gifts) + _GIFTS_PER_PAGE - 1) // _GIFTS_PER_PAGE
    await msg.reply_text(
        f"🎁 *Your Gifts* ({len(gifts)} total · page 1/{total_pages})",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def gifts_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1]
    caller_id = int(parts[2])

    if query.from_user.id != caller_id:
        await query.answer("This isn't your inventory.", show_alert=True)
        return

    await query.answer()

    if action == "page":
        page = int(parts[3])
        gifts = await db.get_user_gifts(config.DB_PATH, caller_id)
        kb = _inv_keyboard(gifts, page=page, user_id=caller_id)
        total_pages = (len(gifts) + _GIFTS_PER_PAGE - 1) // _GIFTS_PER_PAGE
        await query.edit_message_text(
            f"🎁 *Your Gifts* ({len(gifts)} total · page {page + 1}/{total_pages})",
            parse_mode="Markdown",
            reply_markup=kb
        )

    elif action == "detail":
        instance_id = int(parts[3])
        page = int(parts[4])
        instance = await db.get_gift_instance(config.DB_PATH, instance_id)
        if not instance or instance["owner_id"] != caller_id:
            await query.edit_message_text("❌ Gift not found or no longer yours.")
            return
        price_row = await db.get_gift_price(config.DB_PATH, instance["collection"], instance["background"])
        current_price = price_row["current_price"] if price_row else 0
        card = _format_gift_card(instance, current_price)
        back_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back", callback_data=f"gifts:page:{caller_id}:{page}")
        ]])
        await query.edit_message_text(card, parse_mode="Markdown", reply_markup=back_kb)


# ── /gift <collection> <number> [background] ─────────────────────────────────

async def cmd_gift(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if not ctx.args or len(ctx.args) < 2:
        await msg.reply_text(
            "Usage: `/gift <collection> <number> [background]`\nExample: `/gift scared_cat 12 black`",
            parse_mode="Markdown"
        )
        return

    collection = ctx.args[0].lower()
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[1])
    background = ctx.args[2].lower() if len(ctx.args) > 2 else None

    if background and background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    all_gifts = await db.get_user_gifts(config.DB_PATH, user.id)
    matches = [g for g in all_gifts if g["collection"] == collection and g["model_number"] == model_number]
    if not matches:
        await msg.reply_text("❌ You don't own that gift.")
        return

    if background:
        matches = [g for g in matches if g["background"] == background]
        if not matches:
            await msg.reply_text(f"❌ You don't own that gift with a {_bg_label(background)} background.")
            return

    bg_order = {bg: i for i, bg in enumerate(_BACKGROUNDS)}
    instance = min(matches, key=lambda g: bg_order.get(g["background"], 99))

    price_row = await db.get_gift_price(config.DB_PATH, instance["collection"], instance["background"])
    current_price = price_row["current_price"] if price_row else 0
    card = _format_gift_card(instance, current_price)
    await msg.reply_text(f"✨ {display_name(user)} is flexing:\n\n{card}", parse_mode="Markdown")


# ── /shop ─────────────────────────────────────────────────────────────────────

async def cmd_shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    collection = ctx.args[0].lower() if ctx.args else None

    if collection:
        bank_gifts = await db.get_bank_gifts(config.DB_PATH, collection)
        if not bank_gifts:
            col_name = _collection_display_name(collection)
            await msg.reply_text(f"❌ No {col_name} gifts available from the bank right now.")
            return

        lines = [f"🏪 *{_collection_display_name(collection)} — Bank Stock*\n"]
        seen = set()
        for g in bank_gifts:
            key = (g["model_number"], g["background"])
            if key in seen:
                continue
            seen.add(key)
            price_row = await db.get_gift_price(config.DB_PATH, g["collection"], g["background"])
            price = price_row["current_price"] if price_row else 0
            lines.append(
                f"{g['model_emoji']} #{g['model_number']} {g['model_name']} "
                f"{_bg_emoji(g['background'])} {_bg_label(g['background'])} "
                f"— {price:,} WRK$"
            )
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        bank_gifts = await db.get_bank_gifts(config.DB_PATH)
        if not bank_gifts:
            await msg.reply_text("🏪 Bank has no gifts in stock.")
            return
        collections_in_stock = sorted({g["collection"] for g in bank_gifts})
        lines = ["🏪 *Bank Stock — Collections Available*\n"]
        lines += [f"• `{c}` — {_collection_display_name(c)}" for c in collections_in_stock]
        lines.append("\nUse `/shop <collection>` to see models and prices.")
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /buy ──────────────────────────────────────────────────────────────────────

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if len(ctx.args) < 3:
        await msg.reply_text(
            "Usage: `/buy <collection> <number> <background>`\nExample: `/buy scared_cat 12 black`",
            parse_mode="Markdown"
        )
        return

    collection = ctx.args[0].lower()
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[1])
    background = ctx.args[2].lower()

    if background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    instance = await db.get_gift_instance_by_spec(config.DB_PATH, collection, model_number, background)
    if not instance:
        await msg.reply_text("❌ Gift not found.")
        return
    if instance["owner_id"] is not None:
        await msg.reply_text("❌ That gift is already owned by someone. Use `/offer` to trade with them.")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, background)
    if not price_row:
        await msg.reply_text("❌ No price data for that gift.")
        return
    price = price_row["current_price"]

    wallet = await db.get_wallet(config.DB_PATH, user.id)
    if not wallet:
        await msg.reply_text("❌ You don't have a wallet yet. Use `/daily` to create one.")
        return
    if wallet["balance"] < price:
        await msg.reply_text(f"❌ Not enough WRK$. Price: {price:,} · Your balance: {wallet['balance']:,}")
        return

    new_bal = await db.update_balance(config.DB_PATH, user.id, -price)
    await db.transfer_gift(config.DB_PATH, instance["id"], user.id)
    await db.apply_demand_pressure(config.DB_PATH, collection, background, +1)

    col_name = _collection_display_name(collection)
    await msg.reply_text(
        f"✅ Purchased!\n\n"
        f"{instance['model_emoji']} *{col_name} #{model_number}* {_bg_emoji(background)} {_bg_label(background)}\n"
        f"Paid: {price:,} WRK$\n"
        f"💰 Balance: {new_bal:,} WRK$",
        parse_mode="Markdown"
    )


# ── /sell ─────────────────────────────────────────────────────────────────────

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if len(ctx.args) < 3:
        await msg.reply_text(
            "Usage: `/sell <collection> <number> <background>`\nExample: `/sell scared_cat 12 black`",
            parse_mode="Markdown"
        )
        return

    collection = ctx.args[0].lower()
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[1])
    background = ctx.args[2].lower()

    if background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    instance = await db.get_gift_instance_by_spec(config.DB_PATH, collection, model_number, background)
    if not instance or instance["owner_id"] != user.id:
        await msg.reply_text("❌ You don't own that gift.")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, background)
    sell_price = int(price_row["current_price"] * 0.80) if price_row else 0

    await db.transfer_gift(config.DB_PATH, instance["id"], None)
    new_bal = await db.update_balance(config.DB_PATH, user.id, sell_price)
    await db.apply_demand_pressure(config.DB_PATH, collection, background, -1)

    col_name = _collection_display_name(collection)
    await msg.reply_text(
        f"✅ Sold to bank!\n\n"
        f"{instance['model_emoji']} *{col_name} #{model_number}* {_bg_emoji(background)} {_bg_label(background)}\n"
        f"You received: {sell_price:,} WRK$ (80% of market)\n"
        f"💰 Balance: {new_bal:,} WRK$",
        parse_mode="Markdown"
    )


# ── /offer ────────────────────────────────────────────────────────────────────

async def cmd_offer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if len(ctx.args) < 6 or ctx.args[2].lower() != "for":
        await msg.reply_text(
            "Usage: `/offer @username <amount> for <collection> <number> <background>`\n"
            "Example: `/offer @jerry 5000 for scared_cat 12 black`",
            parse_mode="Markdown"
        )
        return

    target_username = ctx.args[0]
    if not ctx.args[1].isdigit():
        await msg.reply_text("❌ Amount must be a number.")
        return
    wrk_amount = int(ctx.args[1])
    collection = ctx.args[3].lower()
    if not ctx.args[4].isdigit():
        await msg.reply_text("❌ Model number must be a number.")
        return
    model_number = int(ctx.args[4])
    background = ctx.args[5].lower()

    if background not in _BACKGROUNDS:
        await msg.reply_text(f"❌ Invalid background. Choose: {', '.join(_BACKGROUNDS)}")
        return

    target_row = await db.get_user_by_username(config.DB_PATH, msg.chat.id, target_username)
    if not target_row:
        await msg.reply_text("❌ Can't find that user. They need to have sent a message here first.")
        return
    target_id = target_row["user_id"]
    target_name = target_row.get("full_name") or target_username

    if target_id == user.id:
        await msg.reply_text("❌ You can't offer to yourself.")
        return

    instance = await db.get_gift_instance_by_spec(config.DB_PATH, collection, model_number, background)
    if not instance:
        await msg.reply_text("❌ Gift not found.")
        return
    if instance["owner_id"] != target_id:
        await msg.reply_text(f"❌ {target_name} doesn't own that gift.")
        return

    wallet = await db.get_wallet(config.DB_PATH, user.id)
    if not wallet or wallet["balance"] < wrk_amount:
        await msg.reply_text(f"❌ You don't have enough WRK$. Balance: {wallet['balance'] if wallet else 0:,}")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, background)
    market_price = price_row["current_price"] if price_row else 0

    offer_id = await db.create_offer(config.DB_PATH, user.id, target_id, instance["id"], wrk_amount)

    col_name = _collection_display_name(collection)
    offer_text = (
        f"💌 *Offer from {display_name(user)}*\n\n"
        f"{instance['model_emoji']} {col_name} #{model_number} "
        f"{_bg_emoji(background)} {_bg_label(background)}\n"
        f"Offer: {wrk_amount:,} WRK$\n"
        f"Market value: {market_price:,} WRK$\n\n"
        f"Offer expires in 24 hours."
    )
    offer_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"gift_offer:accept:{offer_id}:{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"gift_offer:decline:{offer_id}:{user.id}"),
    ]])

    try:
        await ctx.bot.send_message(target_id, offer_text, parse_mode="Markdown", reply_markup=offer_kb)
        await msg.reply_text(f"✅ Offer sent to {target_name}!")
    except TelegramError:
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await msg.reply_text(f"❌ Couldn't DM {target_name}. They need to start a conversation with the bot first.")


async def gift_offer_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1]
    offer_id = int(parts[2])
    from_user_id = int(parts[3])

    offer = await db.get_offer(config.DB_PATH, offer_id)
    if not offer:
        await query.answer("Offer no longer exists.", show_alert=True)
        return
    if offer["status"] != "pending":
        await query.answer(f"Offer already {offer['status']}.", show_alert=True)
        return
    if query.from_user.id != offer["to_user_id"]:
        await query.answer("This offer isn't for you.", show_alert=True)
        return

    instance = await db.get_gift_instance(config.DB_PATH, offer["instance_id"])

    if action == "decline":
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await query.answer()
        await query.edit_message_text("❌ Offer declined.")
        try:
            await ctx.bot.send_message(from_user_id, "❌ Your offer was declined.")
        except TelegramError:
            pass
        return

    # Accept
    if instance["owner_id"] != offer["to_user_id"]:
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await query.answer("You no longer own that gift.", show_alert=True)
        return

    buyer_wallet = await db.get_wallet(config.DB_PATH, from_user_id)
    if not buyer_wallet or buyer_wallet["balance"] < offer["wrk_offered"]:
        await db.update_offer_status(config.DB_PATH, offer_id, "declined")
        await query.answer("The buyer no longer has enough WRK$.", show_alert=True)
        return

    await db.update_balance(config.DB_PATH, from_user_id, -offer["wrk_offered"])
    await db.update_balance(config.DB_PATH, offer["to_user_id"], offer["wrk_offered"])
    await db.transfer_gift(config.DB_PATH, offer["instance_id"], from_user_id)
    await db.update_offer_status(config.DB_PATH, offer_id, "accepted")

    col_name = _collection_display_name(instance["collection"])
    gift_label = f"{instance['model_emoji']} {col_name} #{instance['model_number']} {_bg_emoji(instance['background'])}"

    await query.answer()
    await query.edit_message_text(f"✅ Trade complete! You sold {gift_label} for {offer['wrk_offered']:,} WRK$.")
    try:
        await ctx.bot.send_message(
            from_user_id,
            f"✅ Trade accepted! You received {gift_label}.\nPaid: {offer['wrk_offered']:,} WRK$"
        )
    except TelegramError:
        pass


# ── /offers ───────────────────────────────────────────────────────────────────

async def cmd_offers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    offers = await db.get_offers_for_user(config.DB_PATH, user.id)
    if not offers:
        await msg.reply_text("📭 No pending offers.")
        return

    lines = ["📬 *Pending Offers*\n"]
    for o in offers:
        col_name = _collection_display_name(o["collection"])
        direction = "→ you" if o["to_user_id"] == user.id else "from you"
        lines.append(
            f"{o['model_emoji']} {col_name} #{o['model_number']} "
            f"{_bg_emoji(o['background'])} — {o['wrk_offered']:,} WRK$ ({direction})"
        )
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")
