import logging
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
import db
from utils import is_admin

log = logging.getLogger(__name__)

# Matches [Button Label](https://url) in message text
_BUTTON_RE = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')


def _parse_buttons(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Strip [Label](url) patterns from text and return them as an InlineKeyboardMarkup."""
    matches = list(_BUTTON_RE.finditer(text))
    if not matches:
        return text, None
    clean = _BUTTON_RE.sub("", text).strip()
    rows = [[InlineKeyboardButton(m.group(1), url=m.group(2))] for m in matches]
    return clean, InlineKeyboardMarkup(rows)


def _matches(trigger: str, text: str) -> bool:
    return text.strip().lower() == trigger.lower()


async def on_message_autoreply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    text = msg.text or msg.caption or ""
    if not text or text.startswith("/"):
        return

    autoreplies = await db.get_autoreplies(config.DB_PATH, msg.chat.id)
    for ar in autoreplies:
        if _matches(ar["trigger"], text):
            await _send(ctx.bot, msg.chat.id, ar, reply_to=msg.message_id)
            break  # first match only


async def _send(bot, chat_id: int, ar: dict, reply_to: int | None = None):
    rtype = ar["response_type"]
    content = ar["response_content"]
    caption = ar.get("response_caption") or None

    try:
        if rtype == "text":
            clean, markup = _parse_buttons(content)
            try:
                await bot.send_message(chat_id, clean, parse_mode="Markdown", reply_markup=markup, reply_to_message_id=reply_to)
            except TelegramError:
                await bot.send_message(chat_id, clean, reply_markup=markup, reply_to_message_id=reply_to)
        elif rtype == "photo":
            await bot.send_photo(chat_id, content, caption=caption, reply_to_message_id=reply_to)
        elif rtype == "animation":
            await bot.send_animation(chat_id, content, caption=caption, reply_to_message_id=reply_to)
        elif rtype == "video":
            await bot.send_video(chat_id, content, caption=caption, reply_to_message_id=reply_to)
        elif rtype == "sticker":
            await bot.send_sticker(chat_id, content, reply_to_message_id=reply_to)
        elif rtype == "document":
            await bot.send_document(chat_id, content, caption=caption, reply_to_message_id=reply_to)
    except TelegramError as e:
        log.warning("autoreply failed for trigger %r: %s", ar["trigger"], e)


async def cmd_addautoreply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not ctx.args:
        await msg.reply_text(
            "*Add an autoreply:*\n"
            "• `/addautoreply trigger | response text` — text reply\n"
            "• `/addautoreply trigger` (reply to media) — media reply\n\n"
            "Text supports Markdown and `[Button](url)` for inline buttons.",
            parse_mode="Markdown",
        )
        return

    full = " ".join(ctx.args)
    response_type = "text"
    response_content = None
    response_caption = None

    if "|" in full:
        trigger, _, response_text = full.partition("|")
        trigger = trigger.strip()
        response_content = response_text.strip()
    else:
        trigger = full.strip()
        target = msg.reply_to_message
        if not target:
            await msg.reply_text(
                "Reply to a message or use `trigger | response text` format.", parse_mode="Markdown"
            )
            return
        if target.text:
            response_content = target.text
        elif target.photo:
            response_type = "photo"
            response_content = target.photo[-1].file_id
            response_caption = target.caption
        elif target.animation:
            response_type = "animation"
            response_content = target.animation.file_id
            response_caption = target.caption
        elif target.video:
            response_type = "video"
            response_content = target.video.file_id
            response_caption = target.caption
        elif target.sticker:
            response_type = "sticker"
            response_content = target.sticker.file_id
        elif target.document:
            response_type = "document"
            response_content = target.document.file_id
            response_caption = target.caption
        else:
            await msg.reply_text("Unsupported message type.")
            return

    if not trigger or not response_content:
        await msg.reply_text("Missing trigger or response content.")
        return

    await db.add_autoreply(config.DB_PATH, chat_id, trigger, response_type, response_content, response_caption)
    await msg.reply_text(f"✅ Autoreply set for trigger: `{trigger}`", parse_mode="Markdown")


async def cmd_removeautoreply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id

    if not await is_admin(ctx.bot, chat_id, update.effective_user.id):
        return

    if not ctx.args:
        await msg.reply_text("Usage: `/removeautoreply <trigger>`", parse_mode="Markdown")
        return

    trigger = " ".join(ctx.args)
    removed = await db.remove_autoreply(config.DB_PATH, chat_id, trigger)
    if removed:
        await msg.reply_text(f"✅ Autoreply for `{trigger}` removed.", parse_mode="Markdown")
    else:
        await msg.reply_text(f"No autoreply found for `{trigger}`.", parse_mode="Markdown")


async def cmd_autoreplies(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    rows = await db.get_autoreplies(config.DB_PATH, msg.chat.id)
    if not rows:
        await msg.reply_text("No autoreplies set for this group.")
        return

    lines = [f"🤖 *Autoreplies* ({len(rows)})\n"]
    for ar in rows:
        preview = ar["response_content"]
        if len(preview) > 35:
            preview = preview[:35] + "…"
        lines.append(f"• `{ar['trigger']}` → [{ar['response_type']}] {preview}")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")
