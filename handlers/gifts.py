import random
import time
import logging
from html import escape

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

def _parse_args(args: list[str]) -> tuple[str, list[str]]:
    """Split args into (collection_key, remaining_args).
    Collection key is everything before the first numeric token, joined with '_'.
    Allows '/shop scared cat' and '/shop scared_cat' to both work.
    """
    for i, arg in enumerate(args):
        if arg.isdigit():
            return "_".join(args[:i]).lower(), args[i:]
    return "_".join(args).lower(), []

def _model_emoji_html(instance: dict) -> str:
    eid = instance.get("custom_emoji_id")
    fallback = escape(instance["model_emoji"])
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback

def _format_gift_card(instance: dict, current_price: int) -> str:
    col_name = escape(_collection_display_name(instance["collection"]))
    gn = instance.get("gift_number") or instance["model_number"]
    bg_emoji = _bg_emoji(instance["background"])
    bg_label = escape(_bg_label(instance["background"]))
    bg_mult = _BG_MULTIPLIERS.get(instance["background"], 1.0)
    model_e = _model_emoji_html(instance)
    model_name = escape(instance["model_name"])
    return (
        f"{model_e} <b>{col_name} #{gn}</b>\n\n"
        f"Model: {model_e} {model_name} · {instance['model_rarity_pct']}%\n"
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
        gn = g.get("gift_number") or g["model_number"]
        label = f"{g['model_emoji']} {col_name} #{gn} {_bg_emoji(g['background'])}"
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
        await msg.reply_text("🎁 Your gift inventory is empty. Try /daily or /shop!", parse_mode="HTML")
        return
    kb = _inv_keyboard(gifts, page=0, user_id=user.id)
    total_pages = (len(gifts) + _GIFTS_PER_PAGE - 1) // _GIFTS_PER_PAGE
    await msg.reply_text(
        f"🎁 <b>Your Gifts</b> ({len(gifts)} total · page 1/{total_pages})",
        parse_mode="HTML",
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
            f"🎁 <b>Your Gifts</b> ({len(gifts)} total · page {page + 1}/{total_pages})",
            parse_mode="HTML",
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
        await query.edit_message_text(card, parse_mode="HTML", reply_markup=back_kb)


# ── /gift <collection> <number> ───────────────────────────────────────────────

async def cmd_gift(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if not ctx.args or len(ctx.args) < 2:
        await msg.reply_text(
            "Usage: <code>/gift &lt;collection&gt; &lt;number&gt;</code>\nExample: <code>/gift scared_cat 42</code>",
            parse_mode="HTML"
        )
        return

    collection, rest = _parse_args(ctx.args)
    if not rest or not rest[0].isdigit():
        await msg.reply_text("❌ Gift number must be a number.")
        return
    gift_number = int(rest[0])

    all_gifts = await db.get_user_gifts(config.DB_PATH, user.id)
    instance = next((g for g in all_gifts if g["collection"] == collection and g.get("gift_number") == gift_number), None)
    if not instance:
        await msg.reply_text("❌ You don't own that gift.")
        return

    price_row = await db.get_gift_price(config.DB_PATH, instance["collection"], instance["background"])
    current_price = price_row["current_price"] if price_row else 0
    card = _format_gift_card(instance, current_price)
    await msg.reply_text(f"✨ {display_name(user)} is flexing:\n\n{card}", parse_mode="HTML")


# ── /shop ─────────────────────────────────────────────────────────────────────

async def cmd_shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    collection, _ = _parse_args(ctx.args) if ctx.args else (None, [])

    if collection:
        await _send_shop_models(msg, collection, page=0)
    else:
        await _send_shop_collections(msg, page=0)


_SHOP_COLS_PER_PAGE = 10
_SHOP_MODELS_PER_PAGE = 15


async def _send_shop_collections(msg, page: int):
    bank_gifts = await db.get_bank_gifts(config.DB_PATH)
    if not bank_gifts:
        await msg.reply_text("🏪 Bank has no gifts in stock.")
        return
    collections = sorted({g["collection"] for g in bank_gifts})
    total = len(collections)
    total_pages = (total + _SHOP_COLS_PER_PAGE - 1) // _SHOP_COLS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    chunk = collections[page * _SHOP_COLS_PER_PAGE:(page + 1) * _SHOP_COLS_PER_PAGE]

    # Two-column grid of collection buttons
    rows = []
    for i in range(0, len(chunk), 2):
        row = []
        for col_key in chunk[i:i + 2]:
            row.append(InlineKeyboardButton(
                _collection_display_name(col_key),
                callback_data=f"shop:mdl:{col_key}:0"
            ))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"shop:col:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"shop:col:{page + 1}"))
    if nav:
        rows.append(nav)

    await msg.reply_text(
        f"🏪 <b>Bank Stock</b> — page {page + 1}/{total_pages} · {total} collections",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def _send_shop_models(msg_or_query, collection: str, page: int, edit: bool = False):
    bank_gifts = await db.get_bank_gifts(config.DB_PATH, collection)
    col_name = _collection_display_name(collection)
    if not bank_gifts:
        text = f"❌ No {escape(col_name)} gifts available from the bank right now."
        if edit:
            await msg_or_query.edit_message_text(text, parse_mode="HTML")
        else:
            await msg_or_query.reply_text(text, parse_mode="HTML")
        return

    # Deduplicate to one entry per model_number
    seen: dict[int, dict] = {}
    for g in bank_gifts:
        n = g["model_number"]
        if n not in seen:
            seen[n] = g

    models = sorted(seen.items())  # [(model_number, gift_dict), ...]
    total = len(models)
    total_pages = (total + _SHOP_MODELS_PER_PAGE - 1) // _SHOP_MODELS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    chunk = models[page * _SHOP_MODELS_PER_PAGE:(page + 1) * _SHOP_MODELS_PER_PAGE]

    # Two-column grid of model buttons
    rows = []
    for i in range(0, len(chunk), 2):
        row = []
        for n, g in chunk[i:i + 2]:
            label = f"{g['model_emoji']} #{n} {g['model_name'][:18]}"
            row.append(InlineKeyboardButton(label, callback_data=f"shop:inst:{collection}:{n}"))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"shop:mdl:{collection}:{page - 1}"))
    nav.append(InlineKeyboardButton("⬅️ Collections", callback_data="shop:col:0"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"shop:mdl:{collection}:{page + 1}"))
    rows.append(nav)

    text = f"🏪 <b>{escape(col_name)}</b> — page {page + 1}/{total_pages} · {total} models"
    if edit:
        await msg_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    else:
        await msg_or_query.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def _send_shop_instances(query, collection: str, model_number: int):
    bank_gifts = await db.get_bank_gifts(config.DB_PATH, collection)
    col_name = _collection_display_name(collection)

    instances = [g for g in bank_gifts if g["model_number"] == model_number]
    if not instances:
        await query.edit_message_text(f"❌ No {escape(col_name)} #{model_number} gifts in stock.", parse_mode="HTML")
        return

    prices = {r["background"]: r["current_price"]
              for r in await db.get_all_gift_prices_for_collection(config.DB_PATH, collection)}

    model_e = _model_emoji_html(instances[0])
    model_name = escape(instances[0]["model_name"])
    lines = [f"🏪 <b>{escape(col_name)}</b> · {model_e} {model_name}\n"]
    for g in sorted(instances, key=lambda x: x.get("gift_number") or 0):
        gn = g.get("gift_number") or "?"
        price = prices.get(g["background"], 0)
        lines.append(f"{model_e} {_bg_emoji(g['background'])} — {escape(_bg_label(g['background']))} — {price:,} WRK$ <b>(#{gn})</b>")

    lines.append(f"\n<i>/buy {collection} &lt;#&gt; to purchase</i>")

    back_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Back", callback_data=f"shop:mdl:{collection}:0")
    ]])
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=back_kb)


async def shop_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    # shop:col:<page>  or  shop:mdl:<collection>:<page>
    if parts[1] == "col":
        page = int(parts[2])
        bank_gifts = await db.get_bank_gifts(config.DB_PATH)
        if not bank_gifts:
            await query.edit_message_text("🏪 Bank has no gifts in stock.")
            return
        collections = sorted({g["collection"] for g in bank_gifts})
        total = len(collections)
        total_pages = (total + _SHOP_COLS_PER_PAGE - 1) // _SHOP_COLS_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        chunk = collections[page * _SHOP_COLS_PER_PAGE:(page + 1) * _SHOP_COLS_PER_PAGE]
        rows = []
        for i in range(0, len(chunk), 2):
            row = []
            for col_key in chunk[i:i + 2]:
                row.append(InlineKeyboardButton(
                    _collection_display_name(col_key),
                    callback_data=f"shop:mdl:{col_key}:0"
                ))
            rows.append(row)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"shop:col:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"shop:col:{page + 1}"))
        if nav:
            rows.append(nav)
        await query.edit_message_text(
            f"🏪 <b>Bank Stock</b> — page {page + 1}/{total_pages} · {total} collections",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows)
        )
    elif parts[1] == "mdl":
        collection = parts[2]
        page = int(parts[3])
        await _send_shop_models(query, collection, page, edit=True)
    elif parts[1] == "inst":
        collection = parts[2]
        model_number = int(parts[3])
        await _send_shop_instances(query, collection, model_number)


# ── /buy ──────────────────────────────────────────────────────────────────────

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if len(ctx.args) < 2:
        await msg.reply_text(
            "Usage: <code>/buy &lt;collection&gt; &lt;number&gt;</code>\nExample: <code>/buy scared_cat 42</code>",
            parse_mode="HTML"
        )
        return

    collection, rest = _parse_args(ctx.args)
    if not rest or not rest[0].isdigit():
        await msg.reply_text("❌ Gift number must be a number.")
        return
    gift_number = int(rest[0])

    instance = await db.get_gift_instance_by_number(config.DB_PATH, collection, gift_number)
    if not instance:
        await msg.reply_text("❌ Gift not found.")
        return
    if instance["owner_id"] is not None:
        await msg.reply_text("❌ That gift is already owned by someone. Use /offer to trade with them.")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, instance["background"])
    if not price_row:
        await msg.reply_text("❌ No price data for that gift.")
        return
    price = price_row["current_price"]

    wallet = await db.get_wallet(config.DB_PATH, user.id)
    if not wallet:
        await msg.reply_text("❌ You don't have a wallet yet. Use /daily to create one.")
        return
    if wallet["balance"] < price:
        await msg.reply_text(f"❌ Not enough WRK$. Price: {price:,} · Your balance: {wallet['balance']:,}")
        return

    new_bal = await db.update_balance(config.DB_PATH, user.id, -price)
    await db.transfer_gift(config.DB_PATH, instance["id"], user.id)
    await db.apply_demand_pressure(config.DB_PATH, collection, instance["background"], +1)

    col_name = escape(_collection_display_name(collection))
    await msg.reply_text(
        f"✅ Purchased!\n\n"
        f"{_model_emoji_html(instance)} <b>{col_name} #{gift_number}</b> {_bg_emoji(instance['background'])} {escape(_bg_label(instance['background']))}\n"
        f"Paid: {price:,} WRK$\n"
        f"💰 Balance: {new_bal:,} WRK$",
        parse_mode="HTML"
    )


# ── /sell ─────────────────────────────────────────────────────────────────────

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if len(ctx.args) < 2:
        await msg.reply_text(
            "Usage: <code>/sell &lt;collection&gt; &lt;number&gt;</code>\nExample: <code>/sell scared_cat 42</code>",
            parse_mode="HTML"
        )
        return

    collection, rest = _parse_args(ctx.args)
    if not rest or not rest[0].isdigit():
        await msg.reply_text("❌ Gift number must be a number.")
        return
    gift_number = int(rest[0])

    instance = await db.get_gift_instance_by_number(config.DB_PATH, collection, gift_number)
    if not instance or instance["owner_id"] != user.id:
        await msg.reply_text("❌ You don't own that gift.")
        return

    price_row = await db.get_gift_price(config.DB_PATH, collection, instance["background"])
    sell_price = int(price_row["current_price"] * 0.80) if price_row else 0

    await db.transfer_gift(config.DB_PATH, instance["id"], None)
    new_bal = await db.update_balance(config.DB_PATH, user.id, sell_price)
    await db.apply_demand_pressure(config.DB_PATH, collection, instance["background"], -1)

    col_name = escape(_collection_display_name(collection))
    await msg.reply_text(
        f"✅ Sold to bank!\n\n"
        f"{_model_emoji_html(instance)} <b>{col_name} #{gift_number}</b> {_bg_emoji(instance['background'])} {escape(_bg_label(instance['background']))}\n"
        f"You received: {sell_price:,} WRK$ (80% of market)\n"
        f"💰 Balance: {new_bal:,} WRK$",
        parse_mode="HTML"
    )


# ── /offer ────────────────────────────────────────────────────────────────────

async def cmd_offer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    args = ctx.args
    try:
        for_idx = next(i for i, a in enumerate(args) if a.lower() == "for")
    except StopIteration:
        for_idx = -1

    if for_idx < 2 or len(args) <= for_idx + 1:
        await msg.reply_text(
            "Usage: <code>/offer @username &lt;amount&gt; for &lt;collection&gt; &lt;number&gt;</code>\n"
            "Example: <code>/offer @jerry 5000 for scared cat 42</code>",
            parse_mode="HTML"
        )
        return

    target_username = args[0]
    if not args[1].isdigit():
        await msg.reply_text("❌ Amount must be a number.")
        return
    wrk_amount = int(args[1])
    collection, rest = _parse_args(args[for_idx + 1:])
    if not rest or not rest[0].isdigit():
        await msg.reply_text("❌ Gift number must be a number.")
        return
    gift_number = int(rest[0])

    target_row = await db.get_user_by_username(config.DB_PATH, msg.chat.id, target_username)
    if not target_row:
        await msg.reply_text("❌ Can't find that user. They need to have sent a message here first.")
        return
    target_id = target_row["user_id"]
    target_name = target_row.get("full_name") or target_username

    if target_id == user.id:
        await msg.reply_text("❌ You can't offer to yourself.")
        return

    instance = await db.get_gift_instance_by_number(config.DB_PATH, collection, gift_number)
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

    price_row = await db.get_gift_price(config.DB_PATH, collection, instance["background"])
    market_price = price_row["current_price"] if price_row else 0

    offer_id = await db.create_offer(config.DB_PATH, user.id, target_id, instance["id"], wrk_amount)

    col_name = escape(_collection_display_name(collection))
    offer_text = (
        f"💌 <b>Offer from {escape(display_name(user))}</b>\n\n"
        f"{_model_emoji_html(instance)} {col_name} #{gift_number} "
        f"{_bg_emoji(instance['background'])} {escape(_bg_label(instance['background']))}\n"
        f"Offer: {wrk_amount:,} WRK$\n"
        f"Market value: {market_price:,} WRK$\n\n"
        f"Offer expires in 24 hours."
    )
    offer_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"gift_offer:accept:{offer_id}:{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"gift_offer:decline:{offer_id}:{user.id}"),
    ]])

    try:
        await ctx.bot.send_message(target_id, offer_text, parse_mode="HTML", reply_markup=offer_kb)
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

    col_name = escape(_collection_display_name(instance["collection"]))
    gn = instance.get("gift_number") or instance["model_number"]
    gift_label = f"{_model_emoji_html(instance)} {col_name} #{gn} {_bg_emoji(instance['background'])}"

    await query.answer()
    await query.edit_message_text(f"✅ Trade complete! You sold {gift_label} for {offer['wrk_offered']:,} WRK$.", parse_mode="HTML")
    try:
        await ctx.bot.send_message(
            from_user_id,
            f"✅ Trade accepted! You received {gift_label}.\nPaid: {offer['wrk_offered']:,} WRK$",
            parse_mode="HTML"
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

    lines = ["📬 <b>Pending Offers</b>\n"]
    for o in offers:
        col_name = escape(_collection_display_name(o["collection"]))
        direction = "→ you" if o["to_user_id"] == user.id else "from you"
        gn = o.get("gift_number") or o["model_number"]
        lines.append(
            f"{_model_emoji_html(o)} {col_name} #{gn} "
            f"{_bg_emoji(o['background'])} — {o['wrk_offered']:,} WRK$ ({direction})"
        )
    await msg.reply_text("\n".join(lines), parse_mode="HTML")
