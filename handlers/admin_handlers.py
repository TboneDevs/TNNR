"""Telegram admin command handlers."""

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import ADMIN_IDS, DATABASE_PATH, RAILWAY_VOLUME_MOUNT_PATH
from database.database import db
from services.pool_service import pool_service
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


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = db.diagnostics()
    await update.message.reply_text(
        "✅ TNNR online\n"
        f"Database: {data.get('database')}\n"
        f"Volume: {data.get('volume')}"
    )


def register_admin_handlers(application):
    application.add_handler(CommandHandler("diagnostics", diagnostics))
    application.add_handler(CommandHandler("pool_status", pool_status))
    application.add_handler(CommandHandler("giveaway_status", giveaway_status))
    application.add_handler(CommandHandler("dashboard", dashboard))
    application.add_handler(CommandHandler("health", health))
