"""Telegram admin command handlers."""

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import ADMIN_IDS, ADMIN_LOG_CHANNEL_ID, ANNOUNCEMENT_CHANNEL_ID, DATABASE_PATH, DISCUSSION_GROUP_ID, RAILWAY_VOLUME_MOUNT_PATH
from database.database import db
from services.pool_service import pool_service
from services.direct_delivery_service import direct_delivery_service
from services.credit_event_service import credit_event_service
from utils.channel_utils import ANNOUNCEMENT_CHANNEL_USERNAME, classify_group_error, classify_telegram_error, post_announcement, start_discussion_read_test, verify_discussion_group
from utils.permissions import is_admin
from utils.recovery_manager import recovery_manager


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
async def diagnostics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = db.diagnostics()
    pool = pool_service.get_pool_status()
    issues = recovery_manager.check_database_consistency()
    lines = [
        "🩺 TNNR Diagnostics",
        f"Database: {data.get('database')}",
        f"Database path: {DATABASE_PATH}",
        f"Volume: {data.get('volume')} ({RAILWAY_VOLUME_MOUNT_PATH})",
        f"Migration version: {data.get('migration_version')}",
        f"Active giveaways: {data.get('active_giveaways')}",
        f"Available accounts: {pool.get('available', 0)}",
        f"Reserved accounts: {pool.get('reserved', 0)}",
        f"Delivered accounts: {pool.get('delivered', 0)}",
        f"Admins configured: {len(ADMIN_IDS)}",
        f"Consistency issues: {len(issues)}",
    ]
    await update.message.reply_text("\n".join(lines))


@_admin_only
async def pool_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = pool_service.get_pool_status()
    await update.message.reply_text(
        "📦 Account Pool\n"
        f"Available: {status.get('available', 0)}\n"
        f"Reserved: {status.get('reserved', 0)}\n"
        f"Delivered: {status.get('delivered', 0)}\n"
        f"Invalid: {status.get('invalid', 0)}\n"
        f"Removed: {status.get('removed', 0)}\n"
        f"Total: {status.get('total', 0)}"
    )


@_admin_only
async def giveaway_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.execute_all(
        """SELECT giveaway_id, type, prize, status, created_at
           FROM giveaways ORDER BY created_at DESC LIMIT 10"""
    )
    if not rows:
        await update.message.reply_text("No giveaways found.")
        return
    lines = ["🎁 Recent Giveaways"]
    for row in rows:
        count = db.execute_one("SELECT COUNT(*) FROM entries WHERE giveaway_id = ?", (row[0],))[0]
        lines.append(f"{row[0]} | {row[1]} | {row[3]} | Entries: {count} | Prize: {row[2]}")
    await update.message.reply_text("\n".join(lines))


@_admin_only
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = db.execute_one("SELECT COUNT(*) FROM giveaways WHERE status = 'active'")[0]
    winners = db.execute_one("SELECT COUNT(*) FROM winners")[0]
    pending = db.execute_one("SELECT COUNT(*) FROM winners WHERE claimed_status = 0")[0]
    delivered = db.execute_one("SELECT COUNT(*) FROM winners WHERE claimed_status = 1")[0]
    pool = pool_service.get_pool_status()
    await update.message.reply_text(
        "📊 TNNR Dashboard\n"
        f"Active giveaways: {active}\n"
        f"Total winners: {winners}\n"
        f"Pending claims: {pending}\n"
        f"Delivered claims: {delivered}\n"
        f"Available accounts: {pool.get('available', 0)}"
    )


@_admin_only
async def channeltest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify the bot can post to the configured announcement channel."""
    try:
        message = await context.bot.send_message(
            chat_id=ANNOUNCEMENT_CHANNEL_ID,
            text="✅ Channel Test Successful",
        )
        await update.message.reply_text(
            "✅ Channel test passed.\n\n"
            f"Channel:\n{ANNOUNCEMENT_CHANNEL_USERNAME}\n\n"
            f"Channel ID:\n{ANNOUNCEMENT_CHANNEL_ID}\n\n"
            f"Message ID:\n{message.message_id}"
        )
    except Exception as exc:
        reason = classify_telegram_error(exc)
        await update.message.reply_text(
            "❌ Channel Test Failed\n\n"
            f"Reason: {reason}\n"
            f"Telegram error: {exc}"
        )


@_admin_only
async def discussiontest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify the bot can access/send to the configured discussion group."""
    can_access = "NO"
    can_send = "NO"
    try:
        check = await verify_discussion_group(context.bot)
        can_access = "YES" if check.ok else "NO"
        if not check.ok:
            await update.message.reply_text(
                "❌ Discussion Group Test Failed\n\n"
                f"Reason: {check.reason}\n"
                f"Telegram error: {check.details}"
            )
            return
        message = await context.bot.send_message(
            chat_id=DISCUSSION_GROUP_ID,
            text="✅ Discussion Group Test Successful",
        )
        can_send = "YES"
        start_discussion_read_test(update.effective_user.id, update.effective_chat.id)
        await update.message.reply_text(
            "✅ Discussion group test started.\n\n"
            f"Discussion Group ID:\n{DISCUSSION_GROUP_ID}\n\n"
            f"Can Access:\n{can_access}\n\n"
            f"Can Send Messages:\n{can_send}\n\n"
            "Can Read Messages:\nPENDING LIVE TEST\n\n"
            f"Test Message ID:\n{message.message_id}\n\n"
            "Now send this phrase inside the discussion group or as a channel comment:\n"
            "test trivia access"
        )
    except Exception as exc:
        reason = classify_group_error(exc)
        await update.message.reply_text(
            "❌ Discussion Group Test Failed\n\n"
            f"Reason: {reason}\n"
            f"Can Access: {can_access}\n"
            f"Can Send Messages: {can_send}\n"
            f"Telegram error: {exc}"
        )


@_admin_only
async def creditevent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create and announce a one-time free credit top-up event."""
    admin = update.effective_user
    announcement_text = (
        "🎟️ Free Credit Event!\n\n"
        "Users can receive a one-time event top-up of 3 free credits.\n\n"
        "To claim:\n"
        "1. DM the bot.\n"
        "2. Run /eventclaim.\n\n"
        "These are promotional credits only: use them for /slots or /coinflip. Only gambling winnings become withdrawable credits."
    )
    post = await post_announcement(context.bot, announcement_text)
    if not post.ok:
        await update.message.reply_text(
            "❌ Credit event announcement failed.\n\n"
            f"Reason: {post.reason or 'TELEGRAM_API_ERROR'}\n"
            f"Details: {post.details or 'Unknown error'}"
        )
        return
    result = credit_event_service.create_event(
        admin.id,
        admin.username or getattr(admin, "full_name", None),
        ANNOUNCEMENT_CHANNEL_ID,
        post.message_id,
    )
    if not result.get("success"):
        await update.message.reply_text(f"❌ Credit event could not be created: {result.get('message')}")
        return
    if ADMIN_LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_LOG_CHANNEL_ID,
                text=(
                    "🎟️ Credit event posted\n\n"
                    f"Admin ID: {admin.id}\n"
                    f"Admin username: @{admin.username if admin.username else 'None'}\n"
                    f"Event ID: {result.get('event_id')}\n"
                    f"Credits per user: {result.get('credit_amount')}\n"
                    f"Announcement channel: {ANNOUNCEMENT_CHANNEL_ID}\n"
                    f"Announcement message ID: {post.message_id}"
                ),
            )
        except Exception:
            pass
    await update.message.reply_text(
        "Credit event posted successfully. Users can now claim 3 promotional credits with /eventclaim."
    )


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = db.diagnostics()
    await update.message.reply_text(
        "✅ TNNR online\n"
        f"Database: {data.get('database')}\n"
        f"Volume: {data.get('volume')}"
    )


@_admin_only
async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Assign free unclaimed account credits to a Telegram user."""
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /give TELEGRAM_ID AMOUNT")
        return
    try:
        telegram_id = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Telegram ID and amount must be numbers. Usage: /give 123456789 3")
        return
    if telegram_id <= 0:
        await update.message.reply_text("Telegram ID must be a positive integer.")
        return
    if amount <= 0:
        await update.message.reply_text("Amount must be a positive integer.")
        return
    admin = update.effective_user
    result = direct_delivery_service.admin_give(admin.id, admin.username or admin.full_name, telegram_id, amount)
    if not result.get("success"):
        await update.message.reply_text(f"❌ Could not assign accounts: {result.get('message')}")
        return
    if ADMIN_LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_LOG_CHANNEL_ID,
                text=(
                    "➕ /give allocation created\n\n"
                    f"Admin ID: {admin.id}\n"
                    f"Admin username: @{admin.username if admin.username else 'None'}\n"
                    f"Target Telegram ID: {telegram_id}\n"
                    f"Amount assigned: {amount}\n"
                    f"Pending balance now: {result.get('pending')}"
                ),
            )
        except Exception:
            pass
    await update.message.reply_text(
        "✅ Free account credits assigned.\n\n"
        f"Telegram ID: {telegram_id}\n"
        f"Credits added: {amount}\n"
        f"Unclaimed balance now: {result.get('pending')}\n\n"
        "The user can start the bot in DMs, then run /claim to claim accounts."
    )


def register_admin_handlers(application):
    application.add_handler(CommandHandler("diagnostics", diagnostics))
    application.add_handler(CommandHandler("pool_status", pool_status))
    application.add_handler(CommandHandler("giveaway_status", giveaway_status))
    application.add_handler(CommandHandler("dashboard", dashboard))
    application.add_handler(CommandHandler("health", health))
    application.add_handler(CommandHandler("channeltest", channeltest))
    application.add_handler(CommandHandler("discussiontest", discussiontest))
    application.add_handler(CommandHandler("give", give))
    application.add_handler(CommandHandler("creditevent", creditevent))
