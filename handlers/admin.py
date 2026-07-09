import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import is_admin

log = logging.getLogger(__name__)


# ── Help pages ──────────────────────────────────────────────────────────────────

_PAGES = {
    "mod": (
        "🚨 *Moderation*\n\n"
        "`/warn [reason]` — warn replied user; auto-punishes at limit\n"
        "`/dwarn` — warn + delete the message\n"
        "`/warns` — check warns (reply)\n"
        "`/resetwarns` — clear warns (reply)\n"
        "`/mute [dur] [reason]` — mute (reply)\n"
        "`/dmute` — mute + delete the message\n"
        "`/unmute` — remove mute (reply)\n"
        "`/ban [dur] [reason]` — ban; no duration = permanent\n"
        "`/dban` — ban + delete the message\n"
        "`/unban` — unban (reply)\n"
        "`/kick` — kick (reply)\n"
        "`/dkick` — kick + delete the message\n"
        "`/promote` — make user an admin (reply or `@username`)\n"
        "`/demote` — remove admin rights (reply or `@username`)\n"
        "`/purge [N]` — delete from replied message, or last N messages\n"
        "`/report [reason]` — report a message to admins _(any user)_\n"
        "`/dlog` — delete replied message and forward to log channel\n\n"
        "⏱ _Duration format:_ `30s` · `5m` · `2h` · `1d` · `1w`"
    ),
    "protection": (
        "🛡️ *Protection*\n\n"
        "⚡ *Antiflood*\n"
        "`/setflood <N> [secs]` — trigger after N msgs in window (0 = off)\n"
        "`/setfloodaction <mute|kick|ban> [mute\\_secs]`\n"
        "`/antiflood` — show current settings\n\n"
        "🚫 *Blocklist*\n"
        "`/addblocked <word or phrase>`\n"
        "`/removeblocked <word or phrase>`\n"
        "`/blocklist` — list all blocked patterns\n"
        "`/setblocklistaction <delete|warn|mute|ban>`\n\n"
        "🔒 *Locks*\n"
        "`/lock <type>` · `/unlock <type>` · `/locks`\n"
        "_Types:_ `links` `forwards` `stickers` `photos`\n"
        "`videos` `audios` `documents` `gifs` `polls` `all`\n\n"
        "🚨 *Antiraid*\n"
        "`/antiraid on|off`\n"
        "`/setantiraid <joins> <secs> [mute\\_secs]`\n\n"
        "😇 *Halo* — exempt a user from all protection\n"
        "`/givehalo` · `/removehalo` — reply to a user\n"
        "`/halos` — list all halo users"
    ),
    "group": (
        "👥 *Group*\n\n"
        "`/admins` — list admins with custom titles\n"
        "`/rules` — show rules; reply to a user to DM them the rules\n"
        "`/setrules <text>` — set group rules _(reply to message or inline)_\n"
        "`/me` — your info + warns, sent to your DMs\n"
        "`/report [reason]` — report a message to admins _(any user)_\n\n"
        "👋 *Welcome & Goodbye*\n"
        "`/setwelcome <text|off>` · `/welcome` — view/edit welcome\n"
        "`/setgoodbye <text|off>` · `/goodbye` — view/edit goodbye\n"
        "_Variables:_ `{name}` `{id}` `{mention}` `{username}` `{group}`\n\n"
        "📊 *Activity*\n"
        "`/inactives [days] [kick|ban]` — users silent for N days\n\n"
        "📦 *Settings*\n"
        "`/exportsettings` — download config as JSON\n"
        "`/importsettings` — restore config from JSON file"
    ),
    "autoreply": (
        "🤖 *Autoreplies*\n\n"
        "`/addautoreply trigger | text response`\n"
        "└ Text supports Markdown and `[Button](url)` for inline buttons\n\n"
        "`/addautoreply trigger` _(reply to a photo/gif/sticker/video)_\n"
        "└ Saves that media as the response\n\n"
        "`/removeautoreply <trigger>` — remove a trigger\n"
        "`/autoreplies` — list all triggers for this group\n\n"
        "_Trigger must be the entire message (exact match). First match wins._"
    ),
    "setup": (
        "⚙️ *Setup & Info*\n\n"
        "`/setup` — open per-group config panel\n"
        "`/connect` — configure this group in your DMs\n"
        "`/setlog @channel` — set audit log channel\n"
        "`/cleanservice on|off` — auto-delete join/leave/pin service messages\n\n"
        "ℹ️ *Info*\n"
        "`/id` — show your ID and chat ID\n"
        "`/info` — detailed user info (reply)\n"
        "`/help` — this menu\n"
        "`/econhelp` — economy command reference"
    ),
    "gifts": (
        "🎁 *Gifts*\n\n"
        "Collect, trade, and flex unique animated gift models.\n"
        "Each gift has a *model* (the art style) and a *background* (color rarity).\n\n"
        "🏪 *Shop*\n"
        "`/shop` — browse all collections with inline pages\n"
        "`/shop <collection>` — see models & prices for one collection\n"
        "└ e.g. `/shop scared cat` or `/shop scared_cat`\n\n"
        "🛒 *Buying & Selling*\n"
        "`/buy <collection> <#> <bg>` — buy from bank\n"
        "└ e.g. `/buy scared cat 12 black`\n"
        "`/sell <collection> <#> <bg>` — sell back (80% of market price)\n\n"
        "🎒 *Inventory*\n"
        "`/inv` — browse your gifts with inline pages\n"
        "`/gift <collection> <#> [bg]` — flex a gift in chat\n\n"
        "🤝 *Trading*\n"
        "`/offer @user <amount> for <collection> <#> <bg>`\n"
        "└ Send a WRK$ offer to buy someone's gift\n"
        "`/offers` — view your pending offers\n\n"
        "🎨 *Backgrounds* (rarity multiplier)\n"
        "⬛ Black 3x · 🖤 Onyx 2.5x · 🟣 Grape 2x\n"
        "🟢 Emerald 1.5x · 🔵 Midnight 1.2x · 🟠 Orange 1x\n\n"
        "💰 *Tiers*\n"
        "🟡 High — 1.5M–8M+ WRK$ · 🔵 Mid — 70k–900k · ⚪ Low — 7k–100k"
    ),
    "economy": (
        "💰 *Economy — WRK$*\n\n"
        "💼 *Wallet*\n"
        "`/balance` · `/bal` — check your WRK$ balance and streak\n"
        "`/daily` — claim 500–1500 WRK$ (24h cooldown, streak bonuses)\n"
        "`/leaderboard` · `/lb` — top 10 WRK$ holders globally\n"
        "`/give @user <amount>` — send WRK$ to someone\n\n"
        "🥷 *Crime*\n"
        "`/rob @user` — attempt to rob someone (15m cooldown)\n"
        "└ 50% success · steal 3–10% of their balance\n"
        "└ On fail: fine, bail, or clean getaway\n"
        "└ Victim needs ≥500 WRK$ to be robbable\n"
        "`/hack` — guess a crypto word to crack a wallet (1h cooldown)\n"
        "└ 3 attempts · hints reveal letters on wrong guess\n"
        "└ Reward: 800–2500 WRK$ on success\n"
        "`/guess <word>` — submit your answer during a hack session\n\n"
        "🎰 *Gambling* — use `all` as bet to go all-in\n"
        "`/slots <bet>` — spin the slots (min 10 WRK$)\n"
        "`/coinflip` · `/cf <bet> [heads|tails]` — 50/50 double or nothing\n"
        "`/dice <bet>` — roll vs the bot\n"
        "`/blackjack` · `/bj <bet>` — card game vs the house\n"
        "`/crash <bet>` — start a multiplayer crash game\n"
        "`/cashout` — lock in your multiplier during a crash game\n\n"
        "📅 *Daily Streak Bonuses*\n"
        "Day 7 → 2x · Day 14 → 3x · Day 30+ → 4x"
    ),
}

_PAGE_BACK = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="help:main")]])

_MAIN_TEXT = "🤖 *wrkshelperbot* — pick a section:"

def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚨 Moderation",  callback_data="help:mod"),
            InlineKeyboardButton("🛡️ Protection",   callback_data="help:protection"),
        ],
        [
            InlineKeyboardButton("👥 Group",        callback_data="help:group"),
            InlineKeyboardButton("🤖 Autoreplies",  callback_data="help:autoreply"),
        ],
        [
            InlineKeyboardButton("💰 Economy",      callback_data="help:economy"),
            InlineKeyboardButton("🎁 Gifts",        callback_data="help:gifts"),
        ],
        [
            InlineKeyboardButton("⚙️ Setup & Info", callback_data="help:setup"),
            InlineKeyboardButton("❌ Close",         callback_data="help:close"),
        ],
    ])


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.chat.type in ("group", "supergroup"):
        try:
            await ctx.bot.send_message(
                update.effective_user.id, _MAIN_TEXT,
                parse_mode="Markdown", reply_markup=_main_kb()
            )
            await msg.reply_text("📬 Check your DMs!")
        except TelegramError:
            await msg.reply_text(
                "❌ Couldn't DM you. Start a conversation with me first, then try again."
            )
    else:
        await msg.reply_text(_MAIN_TEXT, parse_mode="Markdown", reply_markup=_main_kb())


async def cmd_econhelp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    content = _PAGES["economy"]
    if msg.chat.type in ("group", "supergroup"):
        try:
            await ctx.bot.send_message(
                update.effective_user.id, content,
                parse_mode="Markdown",
                reply_markup=_PAGE_BACK
            )
            await msg.reply_text("📬 Check your DMs!")
        except TelegramError:
            await msg.reply_text(
                "❌ Couldn't DM you. Start a conversation with me first, then try again."
            )
    else:
        await msg.reply_text(content, parse_mode="Markdown", reply_markup=_PAGE_BACK)


async def help_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = query.data.split(":", 1)[1]

    if page == "close":
        try:
            await query.message.delete()
        except TelegramError:
            pass
        return

    if page == "main":
        await query.edit_message_text(_MAIN_TEXT, parse_mode="Markdown", reply_markup=_main_kb())
        return

    content = _PAGES.get(page)
    if content:
        await query.edit_message_text(content, parse_mode="Markdown", reply_markup=_PAGE_BACK)


async def cmd_setbottopic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if msg.chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ This command only works in groups with topics enabled.")
        return

    thread_id = msg.message_thread_id
    if not thread_id:
        await msg.reply_text("❌ Run this command inside a topic, not in General.")
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, bot_topic_id=thread_id)
    await msg.reply_text(f"✅ Bot topic set. Economy commands will only work here (thread {thread_id}).")


async def cmd_clearbottopic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    await db.update_group(config.DB_PATH, chat_id, bot_topic_id=None)
    await msg.reply_text("✅ Bot topic restriction cleared. Economy commands work everywhere again.")


async def on_any_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Auto-register new groups and track user activity."""
    chat_id = update.effective_chat.id
    await db.upsert_group(config.DB_PATH, chat_id)
    user = update.effective_user
    if user and not user.is_bot:
        await db.update_activity(config.DB_PATH, chat_id, user.id, user.username, user.full_name)


async def on_service_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete join/leave/pin service messages if clean_service_msgs is enabled."""
    msg = update.effective_message
    if not msg:
        return
    group = await db.get_group(config.DB_PATH, msg.chat.id)
    if group and group.get("clean_service_msgs"):
        try:
            await msg.delete()
        except TelegramError:
            pass


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    lines = [f"👤 Your ID: `{update.effective_user.id}`", f"💬 Chat ID: `{msg.chat.id}`"]
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        lines.append(f"🎯 Replied user ID: `{u.id}`")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    target_msg = msg.reply_to_message or msg
    user = target_msg.from_user
    if not user:
        await msg.reply_text("No user found.")
        return
    name = f"@{user.username}" if user.username else user.full_name
    await msg.reply_text(
        f"👤 *User Info*\n\nName: {name}\nID: `{user.id}`\nBot: {'yes' if user.is_bot else 'no'}",
        parse_mode="Markdown",
    )


async def cmd_setlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not ctx.args:
        await msg.reply_text("Usage: `/setlog @channelname` or `/setlog -100xxxxxxxxxx`", parse_mode="Markdown")
        return

    channel = ctx.args[0]
    try:
        chat = await ctx.bot.get_chat(int(channel) if channel.lstrip("-").isdigit() else channel)
        channel_id = chat.id
    except Exception as e:
        await msg.reply_text(f"Couldn't find that channel: {e}")
        return

    await db.upsert_group(config.DB_PATH, chat_id)
    await db.update_group(config.DB_PATH, chat_id, log_channel_id=channel_id)
    await msg.reply_text(f"✅ Log channel set to `{channel}` (`{channel_id}`).", parse_mode="Markdown")


async def cmd_halos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    halos = await db.get_halos(config.DB_PATH, chat_id)
    if not halos:
        await msg.reply_text("No users have a halo in this chat.")
        return

    lines = []
    for h in halos:
        name = h.get("full_name") or h.get("username") or str(h["user_id"])
        lines.append(f"• {name} (`{h['user_id']}`)")

    await msg.reply_text("😇 *Halo users:*\n" + "\n".join(lines), parse_mode="Markdown")
