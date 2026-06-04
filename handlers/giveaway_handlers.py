"""Telegram giveaway command and discussion-entry handlers."""

import logging
import uuid
from datetime import datetime

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from config import ADMIN_LOG_CHANNEL_ID, ANNOUNCEMENT_CHANNEL_ID, DISCUSSION_GROUP_ID
from database.database import db
from services.guess_service import guess_service
from services.lottery_service import lottery_service
from services.trivia_service import trivia_service
from utils.channel_utils import (
    ANNOUNCEMENT_CHANNEL_USERNAME,
    DISCUSSION_TEST_PHRASE,
    clear_discussion_read_test,
    get_discussion_read_targets,
    post_announcement,
    verify_announcement_channel,
    verify_discussion_group,
)
from utils.permissions import is_admin
from utils.validators import normalize_text

logger = logging.getLogger("tnnr.handlers.giveaway")
BLOCKED_LOCATION_MESSAGE = "❌ Giveaway creation commands may only be used in bot DMs or the admin log channel."


def _display_name(user):
    return user.full_name if user and user.full_name else None


def _admin_name(user):
    return user.username or _display_name(user) or str(user.id)


def _new_giveaway_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def _is_allowed_create_location(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    if chat.type == ChatType.PRIVATE or getattr(chat, "type", None) == "private":
        return True
    return bool(ADMIN_LOG_CHANNEL_ID and chat.id == ADMIN_LOG_CHANNEL_ID)


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("Unauthorized admin command.")
            return
        return await func(update, context)
    return wrapper


def _create_success_text(giveaway_id: str, message_id: int) -> str:
    return (
        "✅ Giveaway Created Successfully\n\n"
        "Giveaway ID:\n"
        f"{giveaway_id}\n\n"
        "Announcement Channel:\n"
        f"{ANNOUNCEMENT_CHANNEL_USERNAME}\n\n"
        "Announcement Channel ID:\n"
        f"{ANNOUNCEMENT_CHANNEL_ID}\n\n"
        "Announcement Message ID:\n"
        f"{message_id}\n\n"
        "Discussion Group ID:\n"
        f"{DISCUSSION_GROUP_ID}"
    )


def _create_failure_text(reason: str) -> str:
    return (
        "❌ Failed to post giveaway.\n\n"
        "Reason:\n"
        f"{reason or 'UNKNOWN_ERROR'}"
    )


def _discussion_failure_text(reason: str) -> str:
    return (
        "❌ Failed to verify discussion group access.\n\n"
        "Reason:\n"
        f"{reason or 'UNKNOWN_ERROR'}"
    )


async def _post_or_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, announcement_text: str):
    result = await post_announcement(context.bot, announcement_text)
    if not result.ok:
        await update.message.reply_text(_create_failure_text(result.reason))
        return None
    return result.message_id


async def _verify_channel_or_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    result = await verify_announcement_channel(context.bot)
    if not result.ok:
        await update.message.reply_text(_create_failure_text(result.reason))
        return False
    return True


async def _verify_discussion_or_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    result = await verify_discussion_group(context.bot)
    if not result.ok:
        await update.message.reply_text(_discussion_failure_text(result.reason))
        return False
    return True


def _source_type(message) -> str:
    if getattr(message, "is_automatic_forward", False):
        return "channel_comment"
    origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None)
    if getattr(origin_chat, "id", None) == ANNOUNCEMENT_CHANNEL_ID:
        return "channel_comment"
    return "discussion_group"


def _winner_public_text(result: dict) -> str:
    winner = f"@{result.get('winner_username')}" if result.get("winner_username") else result.get("display_name") or result.get("winner_telegram_id")
    return (
        "🎉 Giveaway Winner!\n\n"
        "Prize:\n"
        f"🏆 {result['prize']}\n\n"
        "Winner:\n"
        f"{winner}\n\n"
        "Telegram ID:\n"
        f"{result['winner_telegram_id']}\n\n"
        "Claim Code:\n"
        f"{result['claim_code']}\n\n"
        "Save/Copy this code.\n\n"
        "Redeem using:\n\n"
        f"/claimcode {result['claim_code']}"
    )


def _winner_admin_text(result: dict) -> str:
    return (
        "🏁 Giveaway winner selected\n\n"
        f"Winner username: @{result.get('winner_username')}\n"
        f"Telegram ID: {result.get('winner_telegram_id')}\n"
        f"First name: {result.get('first_name')}\n"
        f"Last name: {result.get('last_name')}\n"
        f"Display name: {result.get('display_name')}\n"
        f"Prize: {result.get('prize')}\n"
        f"Claim code: {result.get('claim_code')}\n"
        f"Giveaway type: {result.get('giveaway_type')}\n"
        f"Giveaway ID: {result.get('giveaway_id')}\n"
        f"Source message ID: {result.get('source_message_id')}\n"
        f"Timestamp: {datetime.utcnow().isoformat()}Z"
    )


async def _announce_winner(update: Update, context: ContextTypes.DEFAULT_TYPE, result: dict):
    public_text = _winner_public_text(result)
    dm_text = (
        "🎉 Congratulations!\n\n"
        "You won:\n"
        f"🏆 {result['prize']}\n\n"
        "Claim Code:\n"
        f"{result['claim_code']}\n\n"
        "Save/Copy this code.\n\n"
        "Redeem using:\n\n"
        f"/claimcode {result['claim_code']}"
    )
    await context.bot.send_message(chat_id=ANNOUNCEMENT_CHANNEL_ID, text=public_text)
    try:
        await context.bot.send_message(chat_id=result["winner_telegram_id"], text=dm_text)
    except Exception as exc:
        logger.warning("Could not DM winner %s: %s", result["winner_telegram_id"], exc)
    if ADMIN_LOG_CHANNEL_ID:
        await context.bot.send_message(chat_id=ADMIN_LOG_CHANNEL_ID, text=_winner_admin_text(result))
    await update.message.reply_text("✅ Winner selected and announced.")


@_admin_only
async def trivia_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed_create_location(update):
        await update.message.reply_text(BLOCKED_LOCATION_MESSAGE)
        return
    raw = update.message.text.partition(" ")[2]
    parts = [part.strip() for part in raw.split("|", 2)]
    if len(parts) != 3 or not all(parts):
        await update.message.reply_text("Usage: /trivia_create question|answer|prize")
        return
    if not await _verify_channel_or_reply(update, context):
        return
    if not await _verify_discussion_or_reply(update, context):
        return

    question, answer, prize = parts
    giveaway_id = _new_giveaway_id("TRIVIA")
    announcement_text = (
        "🎁 Trivia Giveaway\n\n"
        f"Giveaway ID: {giveaway_id}\n"
        f"Prize: {prize}\n\n"
        f"Question: {question}\n\n"
        "Reply in the linked discussion group or channel comments with your answer to enter."
    )
    message_id = await _post_or_reply(update, context, announcement_text)
    if message_id is None:
        return

    created_id = trivia_service.create_giveaway(
        question, answer, prize, update.effective_user.id, _admin_name(update.effective_user),
        ANNOUNCEMENT_CHANNEL_ID, message_id, giveaway_id, "active", DISCUSSION_GROUP_ID,
    )
    if not created_id:
        await update.message.reply_text("❌ Failed to create giveaway after posting. Check logs.")
        return
    logger.info(
        "Giveaway created admin_id=%s admin_username=%s type=trivia prize=%s channel=%s message_id=%s discussion_group=%s timestamp=%s",
        update.effective_user.id, update.effective_user.username, prize, ANNOUNCEMENT_CHANNEL_ID,
        message_id, DISCUSSION_GROUP_ID, datetime.utcnow().isoformat(),
    )
    await update.message.reply_text(_create_success_text(created_id, message_id))


@_admin_only
async def trivia_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    giveaway_id = context.args[0] if context.args else _latest_giveaway("trivia")
    result = trivia_service.select_winner(giveaway_id, update.effective_user.id, _admin_name(update.effective_user)) if giveaway_id else None
    if not result:
        await update.message.reply_text("No trivia winner could be selected.")
        return
    await _announce_winner(update, context, result)


@_admin_only
async def guess_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed_create_location(update):
        await update.message.reply_text(BLOCKED_LOCATION_MESSAGE)
        return
    if len(context.args) < 4:
        await update.message.reply_text("Usage: /guess_create min max winning_number prize")
        return
    try:
        min_num, max_num, winning = map(int, context.args[:3])
    except ValueError:
        await update.message.reply_text("Min, max, and winning number must be integers.")
        return
    if min_num >= max_num or winning < min_num or winning > max_num:
        await update.message.reply_text("Winning number must be inside a valid min/max range.")
        return
    if not await _verify_channel_or_reply(update, context):
        return
    if not await _verify_discussion_or_reply(update, context):
        return

    prize = " ".join(context.args[3:])
    giveaway_id = _new_giveaway_id("GUESS")
    announcement_text = (
        "🎯 Number Guess Giveaway\n\n"
        f"Giveaway ID: {giveaway_id}\n"
        f"Prize: {prize}\n"
        f"Range: {min_num} - {max_num}\n\n"
        "Reply in the linked discussion group or channel comments with one number to enter."
    )
    message_id = await _post_or_reply(update, context, announcement_text)
    if message_id is None:
        return

    created_id = guess_service.create_giveaway(
        min_num, max_num, winning, prize, update.effective_user.id, _admin_name(update.effective_user),
        ANNOUNCEMENT_CHANNEL_ID, message_id, giveaway_id, "active", DISCUSSION_GROUP_ID,
    )
    if not created_id:
        await update.message.reply_text("❌ Failed to create giveaway after posting. Check logs.")
        return
    logger.info(
        "Giveaway created admin_id=%s admin_username=%s type=guess prize=%s channel=%s message_id=%s discussion_group=%s timestamp=%s",
        update.effective_user.id, update.effective_user.username, prize, ANNOUNCEMENT_CHANNEL_ID,
        message_id, DISCUSSION_GROUP_ID, datetime.utcnow().isoformat(),
    )
    await update.message.reply_text(_create_success_text(created_id, message_id))


@_admin_only
async def guess_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    giveaway_id = context.args[0] if context.args else _latest_giveaway("guess")
    result = guess_service.select_winner(giveaway_id, update.effective_user.id, _admin_name(update.effective_user)) if giveaway_id else None
    if not result:
        await update.message.reply_text("No guess winner could be selected.")
        return
    await _announce_winner(update, context, result)


@_admin_only
async def spin_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed_create_location(update):
        await update.message.reply_text(BLOCKED_LOCATION_MESSAGE)
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /spin_create win_odds prize")
        return
    try:
        win_odds = float(context.args[0])
        if win_odds > 1:
            win_odds = win_odds / 100
    except ValueError:
        await update.message.reply_text("win_odds must be a number, for example 0.25 or 25")
        return
    if not (0 < win_odds <= 1):
        await update.message.reply_text("win_odds must be greater than 0 and no more than 1.0 / 100%")
        return
    if not await _verify_channel_or_reply(update, context):
        return

    prize = " ".join(context.args[1:])
    giveaway_id = _new_giveaway_id("SPIN")
    announcement_text = (
        "🎰 Spin Giveaway\n\n"
        f"Giveaway ID: {giveaway_id}\n"
        f"Prize: {prize}\n"
        f"Win odds: {win_odds * 100:.2f}%\n\n"
        "Reply in the linked discussion group to spin once."
    )
    message_id = await _post_or_reply(update, context, announcement_text)
    if message_id is None:
        return

    created_id = lottery_service.create_giveaway(
        prize, win_odds, update.effective_user.id, _admin_name(update.effective_user),
        ANNOUNCEMENT_CHANNEL_ID, message_id, giveaway_id, "active", DISCUSSION_GROUP_ID,
    )
    if not created_id:
        await update.message.reply_text("❌ Failed to create giveaway after posting. Check logs.")
        return
    logger.info(
        "Giveaway created admin_id=%s admin_username=%s type=spin prize=%s channel=%s message_id=%s discussion_group=%s timestamp=%s",
        update.effective_user.id, update.effective_user.username, prize, ANNOUNCEMENT_CHANNEL_ID,
        message_id, DISCUSSION_GROUP_ID, datetime.utcnow().isoformat(),
    )
    await update.message.reply_text(_create_success_text(created_id, message_id))


@_admin_only
async def giveaway_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /giveaway_stop GIVEAWAY_ID")
        return
    db.execute("UPDATE giveaways SET status = 'ended', active_status = 'ended', ended_at = CURRENT_TIMESTAMP WHERE giveaway_id = ?", (context.args[0],))
    db.commit()
    await update.message.reply_text(f"Giveaway stopped: {context.args[0]}")


def _latest_giveaway(kind):
    row = db.execute_one(
        "SELECT giveaway_id FROM giveaways WHERE type = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (kind,),
    )
    return row[0] if row else None


async def _handle_discussion_read_test(message, user, context: ContextTypes.DEFAULT_TYPE, source_type: str) -> bool:
    if normalize_text(message.text) != DISCUSSION_TEST_PHRASE:
        return False
    targets = get_discussion_read_targets()
    for admin_user_id, notify_chat_id in targets.items():
        sender = f"@{user.username}" if getattr(user, "username", None) else str(user.id)
        await context.bot.send_message(
            chat_id=notify_chat_id,
            text=(
                "✅ Live discussion read test passed.\n\n"
                "Message Received:\n"
                f"{message.text}\n\n"
                "Source:\n"
                f"{source_type}\n\n"
                "Discussion Group ID:\n"
                f"{DISCUSSION_GROUP_ID}\n\n"
                "Sender:\n"
                f"{sender}"
            ),
        )
        clear_discussion_read_test(admin_user_id)
    return bool(targets)


async def collect_discussion_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    if not message or not user or user.is_bot or not message.text or message.text.startswith("/"):
        return
    if message.chat_id != DISCUSSION_GROUP_ID:
        return

    source_type = _source_type(message)
    if await _handle_discussion_read_test(message, user, context, source_type):
        return

    giveaway = db.execute_one(
        "SELECT giveaway_id, type FROM giveaways WHERE status = 'active' AND discussion_group_id = ? ORDER BY created_at DESC LIMIT 1",
        (DISCUSSION_GROUP_ID,),
    )
    if not giveaway:
        return

    first_name = getattr(user, "first_name", None)
    last_name = getattr(user, "last_name", None)
    display_name = _display_name(user)
    if giveaway[1] == "trivia":
        trivia_service.submit_entry(giveaway[0], user.id, user.username, display_name, message.message_id, message.text, first_name, last_name, source_type)
    elif giveaway[1] == "guess":
        guess_service.submit_entry(giveaway[0], user.id, user.username, display_name, message.message_id, message.text, first_name, last_name, source_type)
    elif giveaway[1] == "lottery":
        result = lottery_service.spin_lottery(giveaway[0], user.id, user.username, display_name, message.message_id)
        if result.get("win"):
            await message.reply_text(f"🎉 You won {result['prize']}! Claim Code: {result['claim_code']}")


def register_giveaway_handlers(application):
    application.add_handler(CommandHandler("trivia_create", trivia_create))
    application.add_handler(CommandHandler("trivia_draw", trivia_draw))
    application.add_handler(CommandHandler("guess_create", guess_create))
    application.add_handler(CommandHandler("guess_draw", guess_draw))
    application.add_handler(CommandHandler("spin_create", spin_create))
    application.add_handler(CommandHandler(["giveaway_stop", "trivia_stop", "guess_stop", "spin_stop"], giveaway_stop))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, collect_discussion_entry))
