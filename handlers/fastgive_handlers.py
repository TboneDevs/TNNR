"""Admin-only 60-second flash giveaway command and callback handlers."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from config import ADMIN_LOG_CHANNEL_ID, ANNOUNCEMENT_CHANNEL_ID
from services import fastgive_service
from utils.channel_utils import verify_announcement_channel
from utils.permissions import is_admin

logger = logging.getLogger("tnnr.handlers.fastgive")
FASTGIVE_CALLBACK_PREFIX = "fastgive:"
FASTGIVE_BUTTON_TEXT = "🎉 Enter Giveaway"


def _admin_name(user) -> str:
    return user.username or getattr(user, "full_name", None) or str(user.id)


def _display_name(user) -> Optional[str]:
    return getattr(user, "full_name", None) or getattr(user, "first_name", None)


def _winner_handle(winner: dict) -> str:
    username = winner.get("username")
    if username:
        return f"@{username}"
    return winner.get("display_name") or winner.get("first_name") or str(winner.get("telegram_id"))


def _allowed_admin_location(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    if chat.type == ChatType.PRIVATE or getattr(chat, "type", None) == "private":
        return True
    return bool(ADMIN_LOG_CHANNEL_ID and chat.id == ADMIN_LOG_CHANNEL_ID)


def _entry_markup(giveaway_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(FASTGIVE_BUTTON_TEXT, callback_data=f"{FASTGIVE_CALLBACK_PREFIX}{giveaway_id}")]])


def countdown_text(prize: str, seconds: int, entries: int) -> str:
    return (
        "⚡ FLASH GIVEAWAY ⚡\n\n"
        f"🎁 Prize: {prize}\n\n"
        f"⏰ Ends in: {seconds} Seconds\n\n"
        f"👥 Entries: {entries}\n\n"
        "Click below to enter!"
    )


def ended_text(prize: str, count: int, winner: dict) -> str:
    return (
        "🎉 GIVEAWAY ENDED 🎉\n\n"
        f"🎁 Prize: {prize}\n\n"
        f"👥 Total Entries: {count}\n\n"
        f"🏆 Winner: {_winner_handle(winner)}\n"
        f"🆔 ID: {winner.get('telegram_id')}\n\n"
        "Congratulations!"
    )


def cancelled_text() -> str:
    return (
        "❌ GIVEAWAY CANCELLED\n\n"
        "Reason:\n"
        "No valid entries were received.\n\n"
        "No winner was selected."
    )


def winner_dm_text(prize: str) -> str:
    return (
        "🎉 Congratulations!\n\n"
        "You won:\n\n"
        f"🎁 {prize}\n\n"
        "Please contact an administrator if needed to receive your prize."
    )


def winner_channel_text(prize: str, winner: dict) -> str:
    return (
        "🎉 Giveaway Winner!\n\n"
        f"🏆 Congratulations {_winner_handle(winner)}\n\n"
        "You won:\n"
        f"🎁 {prize}\n\n"
        "Please check your DMs."
    )


def fastgive_log_text(giveaway: dict, total_entries: int, winner: Optional[dict], creator_name: Optional[str], outcome: str) -> str:
    return (
        "📊 FAST GIVEAWAY LOG\n\n"
        f"🆔 Giveaway ID: {giveaway.get('giveaway_id')}\n"
        f"🎁 Prize: {giveaway.get('prize')}\n"
        f"👤 Created By: {creator_name or giveaway.get('creator_name') or giveaway.get('creator_id')}\n"
        f"🕒 Start Time: {giveaway.get('start_at')}\n"
        f"🕒 End Time: {datetime.utcnow().isoformat()}Z\n"
        f"👥 Entries: {total_entries}\n"
        f"🏁 Outcome: {outcome}\n"
        f"🏆 Winner: {_winner_handle(winner) if winner else 'None'}\n"
        f"🆔 Winner ID: {winner.get('telegram_id') if winner else 'None'}"
    )


async def _send_admin_log(bot, text: str):
    if not ADMIN_LOG_CHANNEL_ID:
        return
    try:
        await bot.send_message(chat_id=ADMIN_LOG_CHANNEL_ID, text=text)
    except Exception as exc:
        logger.warning("Failed to send fastgive admin log: %s", exc)


async def _edit_with_retry(bot, chat_id: int, message_id: int, text: str, reply_markup=None, attempts: int = 3) -> bool:
    delay = 0.5
    for attempt in range(attempts):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
            return True
        except Exception as exc:
            logger.warning("Fastgive message edit failed attempt %s/%s: %s", attempt + 1, attempts, exc)
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
                delay *= 2
    return False


async def _run_countdown_and_finish(bot, giveaway_id: str):
    try:
        for seconds in fastgive_service.FASTGIVE_UPDATE_SECONDS:
            await asyncio.sleep(10)
            giveaway = fastgive_service.get_giveaway(giveaway_id)
            if not giveaway or giveaway["status"] != "active":
                return
            count = fastgive_service.entry_count(giveaway_id)
            await _edit_with_retry(
                bot,
                giveaway["announcement_channel_id"],
                giveaway["announcement_message_id"],
                countdown_text(giveaway["prize"], seconds, count),
                reply_markup=_entry_markup(giveaway_id),
            )
        await asyncio.sleep(10)
        await finalize_fastgive(bot, giveaway_id)
    except Exception as exc:
        logger.exception("Fastgive countdown task failed for %s: %s", giveaway_id, exc)


async def finalize_fastgive(bot, giveaway_id: str):
    final = fastgive_service.close_for_finalization(giveaway_id)
    if not final.get("success"):
        return final
    giveaway = final["giveaway"]
    entries = final["entries"]
    total_entries = len(entries)
    winner = fastgive_service.choose_winner(entries)
    if not winner:
        fastgive_service.mark_cancelled(giveaway_id, total_entries)
        await _edit_with_retry(
            bot,
            giveaway["announcement_channel_id"],
            giveaway["announcement_message_id"],
            cancelled_text(),
            reply_markup=None,
        )
        await _send_admin_log(bot, fastgive_log_text(giveaway, total_entries, None, giveaway.get("creator_name"), "cancelled_no_entries"))
        return {"success": True, "status": "cancelled", "entries": 0}

    fastgive_service.mark_ended(giveaway_id, winner, total_entries)
    await _edit_with_retry(
        bot,
        giveaway["announcement_channel_id"],
        giveaway["announcement_message_id"],
        ended_text(giveaway["prize"], total_entries, winner),
        reply_markup=None,
    )
    dm_sent = False
    try:
        await bot.send_message(chat_id=winner["telegram_id"], text=winner_dm_text(giveaway["prize"]))
        dm_sent = True
    except Exception as exc:
        logger.warning("Could not DM fastgive winner %s: %s", winner.get("telegram_id"), exc)
    try:
        await bot.send_message(chat_id=giveaway["announcement_channel_id"], text=winner_channel_text(giveaway["prize"], winner))
    except Exception as exc:
        logger.warning("Could not send fastgive winner channel announcement: %s", exc)
    await _send_admin_log(
        bot,
        fastgive_log_text(giveaway, total_entries, winner, giveaway.get("creator_name"), f"winner_selected; winner_dm_sent={'yes' if dm_sent else 'no'}"),
    )
    return {"success": True, "status": "ended", "winner": winner, "entries": total_entries}


async def fastgive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("Unauthorized admin command.")
        return
    if not _allowed_admin_location(update):
        await update.message.reply_text("❌ /fastgive may only be used in bot DMs or the admin log channel.")
        return
    prize = update.message.text.partition(" ")[2].strip() if update.message and update.message.text else ""
    if not prize:
        await update.message.reply_text("Usage: /fastgive PRIZE")
        return
    channel_check = await verify_announcement_channel(context.bot)
    if not channel_check.ok:
        await update.message.reply_text(
            "❌ Failed to post fast giveaway.\n\n"
            f"Reason: {channel_check.reason or 'TELEGRAM_API_ERROR'}\n"
            f"Details: {channel_check.details or 'Unknown error'}"
        )
        return
    giveaway_id = fastgive_service.new_fastgive_id()
    try:
        message = await context.bot.send_message(
            chat_id=ANNOUNCEMENT_CHANNEL_ID,
            text=countdown_text(prize, 60, 0),
            reply_markup=_entry_markup(giveaway_id),
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ Failed to post fast giveaway.\n\nReason: TELEGRAM_API_ERROR\nDetails: {exc}")
        return
    created = fastgive_service.create_fast_giveaway(
        giveaway_id=giveaway_id,
        prize=prize,
        creator_id=user.id,
        creator_name=_admin_name(user),
        announcement_channel_id=ANNOUNCEMENT_CHANNEL_ID,
        announcement_message_id=message.message_id,
    )
    if not created.get("success"):
        await update.message.reply_text(f"❌ Fast giveaway could not be stored: {created.get('message')}")
        return
    app = getattr(context, "application", None)
    if app and hasattr(app, "create_task"):
        app.create_task(_run_countdown_and_finish(context.bot, giveaway_id), name=f"fastgive:{giveaway_id}")
    else:
        asyncio.create_task(_run_countdown_and_finish(context.bot, giveaway_id))
    await _send_admin_log(
        context.bot,
        (
            "⚡ Fast giveaway started\n\n"
            f"Giveaway ID: {giveaway_id}\n"
            f"Prize: {prize}\n"
            f"Created By: {_admin_name(user)} ({user.id})\n"
            f"Announcement message ID: {message.message_id}\n"
            "Duration: 60 seconds"
        ),
    )
    await update.message.reply_text(
        "✅ Fast giveaway started.\n\n"
        f"Giveaway ID: {giveaway_id}\n"
        f"Prize: {prize}\n"
        f"Duration: 60 seconds\n"
        f"Announcement Message ID: {message.message_id}"
    )


async def fastgive_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(FASTGIVE_CALLBACK_PREFIX):
        return
    giveaway_id = query.data.split(":", 1)[1]
    user = query.from_user
    if not user or getattr(user, "is_bot", False):
        await query.answer("Bots cannot enter this giveaway.", show_alert=False)
        return
    result = fastgive_service.add_entry(
        giveaway_id,
        user.id,
        getattr(user, "username", None),
        getattr(user, "first_name", None),
        getattr(user, "last_name", None),
        _display_name(user),
    )
    if result.get("success"):
        await query.answer("👍", show_alert=False)
    elif result.get("status") == "duplicate":
        await query.answer("You already entered this giveaway.", show_alert=False)
    elif result.get("status") in {"closed", "not_found"}:
        await query.answer("This giveaway is closed.", show_alert=False)
    else:
        await query.answer("Could not enter right now. Please try again.", show_alert=False)


async def _resume_fastgive_finish(bot, giveaway_id: str, remaining_seconds: float):
    """Resume finalization after restart; countdown edits are best-effort while process is alive."""
    if remaining_seconds > 0:
        await asyncio.sleep(remaining_seconds)
    await finalize_fastgive(bot, giveaway_id)


async def recover_fastgive_tasks(application):
    """Reschedule active fast giveaways after a bot restart.

    Active giveaways are persisted in SQLite. If Railway restarts during a
    60-second giveaway, the bot reschedules finalization based on the stored
    end_at timestamp. Countdown edits that were missed during downtime are not
    replayed, but the giveaway is still ended/cancelled safely and only once.
    """
    rows = application.bot_data.get("_fastgive_recovery_rows") if hasattr(application, "bot_data") else None
    if rows is None:
        from database.database import db
        rows = [dict(row) for row in db.execute_all("SELECT * FROM fast_giveaways WHERE status = 'active'")]
    for row in rows:
        try:
            end_at = datetime.fromisoformat(row["end_at"])
        except Exception:
            remaining = 0
        else:
            remaining = max(0.0, (end_at - datetime.utcnow()).total_seconds())
        application.create_task(
            _resume_fastgive_finish(application.bot, row["giveaway_id"], remaining),
            name=f"fastgive-recover:{row['giveaway_id']}",
        )


def register_fastgive_handlers(application):
    application.add_handler(CommandHandler("fastgive", fastgive))
    application.add_handler(CallbackQueryHandler(fastgive_entry, pattern=f"^{FASTGIVE_CALLBACK_PREFIX}"))
