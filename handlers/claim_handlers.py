"""Telegram claim and pool handlers."""

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from services.claim_service import claim_service
from services.pool_service import pool_service
from utils.permissions import is_admin

AWAITING_POOL_UPLOAD = set()


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("Unauthorized admin command.")
            return
        return await func(update, context)
    return wrapper


async def claimcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /claimcode CPM-XXXXXX")
        return
    code = context.args[0].strip()
    user = update.effective_user
    if is_admin(user.id):
        status = claim_service.get_claim_status(code)
        if status.get("found"):
            await update.message.reply_text(
                f"Claim: {status['code']}\nWinner: {status['winner']} ({status['winner_id']})\n"
                f"Prize: {status['prize']}\nClaimed: {status['claimed']}\nClaimed at: {status['claimed_at']}"
            )
            return
    result = claim_service.redeem_claim_code(code, user.id, user.username or user.full_name)
    if not result["success"]:
        await update.message.reply_text(result["message"])
        return
    lines = ["✅ Prize Delivered Successfully", f"Claim Code: {code}", "Your Accounts:"]
    lines.extend(f"{idx}. {account}" for idx, account in enumerate(result["accounts"], start=1))
    lines.append("Please save these credentials immediately.")
    await update.message.reply_text("\n".join(lines))


@_admin_only
async def admin_upload_pool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    AWAITING_POOL_UPLOAD.add(update.effective_user.id)
    await update.message.reply_text("Upload a .txt file with one email:password account per line.")


async def receive_pool_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in AWAITING_POOL_UPLOAD or not update.message.document:
        return
    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("Only .txt pool uploads are accepted.")
        return
    tg_file = await context.bot.get_file(doc.file_id)
    raw = await tg_file.download_as_bytearray()
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    result = pool_service.import_accounts(lines, user.id, user.username or user.full_name)
    AWAITING_POOL_UPLOAD.discard(user.id)
    await update.message.reply_text(
        "✅ Upload Complete\n"
        f"Accounts Added: {result['added']}\n"
        f"Duplicates: {result['duplicates']}\n"
        f"Invalid Lines: {result['invalid']}\n"
        f"Pool Total: {result['total']}"
    )


@_admin_only
async def pool_add_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    line = " ".join(context.args)
    result = pool_service.import_accounts([line], update.effective_user.id, update.effective_user.username)
    await update.message.reply_text(f"Added: {result['added']} | Duplicates: {result['duplicates']} | Invalid: {result['invalid']}")


@_admin_only
async def pool_mark_invalid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /pool_mark_invalid email@example.com")
        return
    success = pool_service.mark_invalid(context.args[0], update.effective_user.id, update.effective_user.username)
    await update.message.reply_text("Marked invalid." if success else "Could not mark invalid.")


def register_claim_handlers(application):
    application.add_handler(CommandHandler("claimcode", claimcode))
    application.add_handler(CommandHandler("admin_upload_pool", admin_upload_pool))
    application.add_handler(CommandHandler("pool_add_single", pool_add_single))
    application.add_handler(CommandHandler("pool_mark_invalid", pool_mark_invalid))
    application.add_handler(MessageHandler(filters.Document.ALL, receive_pool_file))
