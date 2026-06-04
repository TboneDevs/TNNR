"""Telegram giveaway command and entry handlers."""

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from config import DISCUSSION_GROUP_ID
from database.database import db
from services.guess_service import guess_service
from services.trivia_service import trivia_service
from utils.permissions import is_admin


def _display_name(user):
    return user.full_name if user and user.full_name else None


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("Unauthorized admin command.")
            return
        return await func(update, context)
    return wrapper


@_admin_only
async def trivia_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.partition(" ")[2]
    parts = [part.strip() for part in raw.split("|", 2)]
    if len(parts) != 3 or not all(parts):
        await update.message.reply_text("Usage: /trivia_create question|answer|prize")
        return
    giveaway_id = trivia_service.create_giveaway(parts[0], parts[1], parts[2], update.effective_user.id, update.effective_user.username)
    await update.message.reply_text(f"Trivia giveaway created: {giveaway_id}")


@_admin_only
async def trivia_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    giveaway_id = context.args[0] if context.args else _latest_giveaway("trivia")
    result = trivia_service.select_winner(giveaway_id, update.effective_user.id, update.effective_user.username) if giveaway_id else None
    if not result:
        await update.message.reply_text("No trivia winner could be selected.")
        return
    await update.message.reply_text(f"Winner: @{result['winner_username']}\nClaim Code: {result['claim_code']}\nPrize: {result['prize']}")


@_admin_only
async def guess_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 4:
        await update.message.reply_text("Usage: /guess_create min max winning_number prize")
        return
    try:
        min_num, max_num, winning = map(int, context.args[:3])
    except ValueError:
        await update.message.reply_text("Min, max, and winning number must be integers.")
        return
    prize = " ".join(context.args[3:])
    giveaway_id = guess_service.create_giveaway(min_num, max_num, winning, prize, update.effective_user.id, update.effective_user.username)
    await update.message.reply_text(f"Number guess giveaway created: {giveaway_id}")


@_admin_only
async def guess_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    giveaway_id = context.args[0] if context.args else _latest_giveaway("guess")
    result = guess_service.select_winner(giveaway_id, update.effective_user.id, update.effective_user.username) if giveaway_id else None
    if not result:
        await update.message.reply_text("No guess winner could be selected.")
        return
    await update.message.reply_text(f"Winner: @{result['winner_username']}\nGuess: {result['guess']}\nClaim Code: {result['claim_code']}\nPrize: {result['prize']}")


@_admin_only
async def giveaway_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /giveaway_stop GIVEAWAY_ID")
        return
    db.execute("UPDATE giveaways SET status = 'ended', ended_at = CURRENT_TIMESTAMP WHERE giveaway_id = ?", (context.args[0],))
    db.commit()
    await update.message.reply_text(f"Giveaway stopped: {context.args[0]}")


def _latest_giveaway(kind):
    row = db.execute_one(
        "SELECT giveaway_id FROM giveaways WHERE type = ? ORDER BY created_at DESC LIMIT 1",
        (kind,),
    )
    return row[0] if row else None


async def collect_discussion_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    if not message or not user or user.is_bot or not message.text or message.text.startswith("/"):
        return
    if DISCUSSION_GROUP_ID and message.chat_id != DISCUSSION_GROUP_ID:
        return
    giveaway = db.execute_one(
        "SELECT giveaway_id, type FROM giveaways WHERE status IN ('active', 'draft') ORDER BY created_at DESC LIMIT 1"
    )
    if not giveaway:
        return
    if giveaway[1] == "trivia":
        trivia_service.submit_entry(giveaway[0], user.id, user.username, _display_name(user), message.message_id, message.text)
    elif giveaway[1] == "guess":
        guess_service.submit_entry(giveaway[0], user.id, user.username, _display_name(user), message.message_id, message.text)


def register_giveaway_handlers(application):
    application.add_handler(CommandHandler("trivia_create", trivia_create))
    application.add_handler(CommandHandler("trivia_draw", trivia_draw))
    application.add_handler(CommandHandler("guess_create", guess_create))
    application.add_handler(CommandHandler("guess_draw", guess_draw))
    application.add_handler(CommandHandler(["giveaway_stop", "trivia_stop", "guess_stop", "spin_stop"], giveaway_stop))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, collect_discussion_entry))
