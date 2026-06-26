import time
from datetime import datetime, timezone
from telegram import Bot
from telegram.error import TelegramError
from utils import format_duration

_EMOJI = {
    "ban": "🔨", "mute": "🔇", "kick": "👢", "warn": "⚠️",
    "unban": "✅", "unmute": "🔊", "resetwarns": "🔄",
}

def build_log_message(
    action: str,
    target_id: int,
    target_name: str,
    admin_name: str,
    group_id: int,
    group_name: str,
    reason: str | None = None,
    duration_secs: int | None = None,
    warn_count: int | None = None,
    warn_limit: int | None = None,
) -> str:
    emoji = _EMOJI.get(action.lower(), "🔧")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if action.lower() == "warn" and warn_count is not None and warn_limit is not None:
        header = f"⚠️ Warn {warn_count}/{warn_limit} — {target_name} ({target_id})"
    else:
        header = f"{emoji} {action.title()} — {target_name} ({target_id})"

    lines = [
        header,
        f"👮 Admin: {admin_name}",
    ]
    if reason:
        lines.append(f"💬 Reason: {reason}")
    if duration_secs:
        expiry_ts = int(time.time()) + duration_secs
        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"⏱ Duration: {format_duration(duration_secs)} (expires {expiry_dt})")
    lines.append(f"🗂 Group: {group_name} ({group_id})")
    lines.append(f"🕐 {now}")
    return "\n".join(lines)


async def log_action(bot: Bot, log_channel_id: int | None, **kwargs) -> None:
    """Send a log message to the log channel. Silently skips if no channel configured."""
    if not log_channel_id:
        return
    text = build_log_message(**kwargs)
    try:
        await bot.send_message(chat_id=log_channel_id, text=text)
    except TelegramError:
        pass
