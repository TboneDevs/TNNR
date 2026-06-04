"""Telegram claim, user, and pool handlers."""

from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from config import ADMIN_LOG_CHANNEL_ID
from services.claim_service import claim_service
from services.pool_service import pool_service
from utils.permissions import is_admin

AWAITING_POOL_UPLOAD = set()
BOT_USERNAME = "@AccountTool_Bot"


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("Unauthorized admin command.")
            return
        return await func(update, context)
    return wrapper


def _format_won_at(value) -> str:
    if not value:
        return "Unknown"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %I:%M %p").replace(" 0", " ")
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text.split("+")[0], fmt).strftime("%Y-%m-%d %I:%M %p").replace(" 0", " ")
        except ValueError:
            continue
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to TNNR Giveaways.\n\n"
        "🎟️ If you won a giveaway, run:\n\n"
        "/mycodes\n\n"
        "to view your unclaimed claim codes.\n\n"
        "Then redeem using:\n\n"
        "/claimcode CPM-XXXXX"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "TNNR Bot Help\n\n"
        "User Commands:\n\n"
        "/mycodes\n"
        "View your unclaimed giveaway claim codes.\n\n"
        "/claimcode CPM-XXXXX\n"
        "Redeem a claim code and automatically receive your prize."
    )


async def mycodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lookup-only list of the caller's unclaimed codes."""
    user = update.effective_user
    codes = claim_service.list_unclaimed_codes(user.id)
    if not codes:
        await update.message.reply_text(
            "🎟️ My Claim Codes\n\n"
            "You do not currently have any unclaimed codes.\n\n"
            "If you recently won, make sure you are using the same Telegram account that entered the giveaway."
        )
        return

    lines = [
        "🎟️ My Unclaimed Claim Codes",
        "",
        f"You have {len(codes)} unclaimed code(s):",
        "",
    ]
    for idx, code in enumerate(codes, start=1):
        lines.extend([
            f"{idx}. Claim Code:",
            f"   {code['claim_code']}",
            "",
            "Prize:",
            str(code.get('prize') or 'Unknown'),
            "",
            "Giveaway:",
            str(code.get('giveaway_type') or 'Giveaway'),
            "",
            "Won:",
            _format_won_at(code.get('won_at')),
            "",
            "Redeem with:",
            f"/claimcode {code['claim_code']}",
            "",
        ])
    await update.message.reply_text("\n".join(lines).rstrip())


async def _send_redemption_admin_log(context: ContextTypes.DEFAULT_TYPE, user, result: dict):
    if not ADMIN_LOG_CHANNEL_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=ADMIN_LOG_CHANNEL_ID,
            text=(
                "✅ Claim code redeemed\n\n"
                f"Claim code: {result.get('claim_code')}\n"
                f"Telegram ID: {user.id}\n"
                f"Username: @{user.username if user.username else 'None'}\n"
                f"Display name: {getattr(user, 'full_name', None)}\n"
                f"Prize: {result.get('prize')}\n"
                f"Accounts delivered: {result.get('accounts_delivered')}\n"
                f"Timestamp: {datetime.utcnow().isoformat()}Z"
            ),
        )
    except Exception:
        # Redemption must not fail just because the admin-log message failed.
        return


async def claimcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /claimcode CPM-XXXXXX")
        return
    chat = update.effective_chat
    if getattr(chat, "type", None) != "private":
        await update.message.reply_text("❌ Claim codes can only be redeemed in a private DM with the bot.")
        return
    raw_code = " ".join(context.args)
    user = update.effective_user
    if is_admin(user.id):
        status = claim_service.get_claim_status(raw_code)
        if status.get("found"):
            await update.message.reply_text(
                f"Claim: {status['code']}\nWinner: {status['winner']} ({status['winner_id']})\n"
                f"Prize: {status['prize']}\nClaimed: {status['claimed']}\nClaimed at: {status['claimed_at']}"
            )
            return
    result = claim_service.redeem_claim_code(raw_code, user.id, user.username or user.full_name)
    if not result["success"]:
        await update.message.reply_text(result["message"])
        return
    delivered_code = result.get("claim_code") or raw_code
    lines = ["✅ Prize Delivered Successfully", f"Claim Code: {delivered_code}", "Your Accounts:"]
    lines.extend(f"{idx}. {account}" for idx, account in enumerate(result["accounts"], start=1))
    lines.append("Please save these credentials immediately.")
    await update.message.reply_text("\n".join(lines))
    await _send_redemption_admin_log(context, user, result)


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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("mycodes", mycodes))
    application.add_handler(CommandHandler("claimcode", claimcode))
    application.add_handler(CommandHandler("admin_upload_pool", admin_upload_pool))
    application.add_handler(CommandHandler("pool_add_single", pool_add_single))
    application.add_handler(CommandHandler("pool_mark_invalid", pool_mark_invalid))
    application.add_handler(MessageHandler(filters.Document.ALL, receive_pool_file))
