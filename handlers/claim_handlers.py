"""Telegram claim, user, and pool handlers."""

from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from config import ADMIN_LOG_CHANNEL_ID
from services.bonus_service import bonus_service
from services.claim_service import claim_service
from services.direct_delivery_service import direct_delivery_service
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


async def _reply_with_direct_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE, trigger: str = "dm"):
    """Attempt direct owed-account delivery and reply with the outcome."""
    user = update.effective_user
    result = direct_delivery_service.attempt_delivery_for_user(
        user.id, user.username or getattr(user, "full_name", None), trigger=trigger
    )
    if result.get("status") == "delivered":
        lines = [
            "✅ Pending Accounts Delivered",
            f"Accounts delivered: {result.get('accounts_delivered', 0)}",
            "",
            "Your Accounts:",
        ]
        lines.extend(f"{idx}. {account}" for idx, account in enumerate(result.get("accounts", []), start=1))
        lines.append("")
        lines.append("Please save these credentials immediately.")
        await update.message.reply_text("\n".join(lines))
        await _send_direct_delivery_admin_log(context, user, result)
        return result
    if result.get("status") == "insufficient_stock":
        await update.message.reply_text(
            "⏳ You have pending accounts, but stock is temporarily unavailable.\n\n"
            "Your balance was not reduced. Please try again later."
        )
        await _send_direct_delivery_admin_log(context, user, result)
        return result
    if result.get("status") == "error":
        await update.message.reply_text("❌ Delivery could not be completed right now. Please try again later.")
        return result
    await update.message.reply_text("You have no pending accounts to receive right now.")
    return result


async def _send_direct_delivery_admin_log(context: ContextTypes.DEFAULT_TYPE, user, result: dict):
    if not ADMIN_LOG_CHANNEL_ID:
        return
    try:
        if result.get("status") == "delivered":
            text = (
                "✅ Direct account delivery completed\n\n"
                f"Telegram ID: {user.id}\n"
                f"Username: @{user.username if user.username else 'None'}\n"
                f"Display name: {getattr(user, 'full_name', None)}\n"
                f"Accounts delivered: {result.get('accounts_delivered')}\n"
                f"Delivery ref: {result.get('delivery_ref')}\n"
                f"Timestamp: {datetime.utcnow().isoformat()}Z"
            )
        else:
            text = (
                "⚠️ Direct account delivery failed\n\n"
                f"Telegram ID: {user.id}\n"
                f"Username: @{user.username if user.username else 'None'}\n"
                f"Pending amount: {result.get('owed_amount')}\n"
                f"Available stock: {result.get('available')}\n"
                f"Reason: {result.get('status')}\n"
                f"Timestamp: {datetime.utcnow().isoformat()}Z"
            )
        await context.bot.send_message(chat_id=ADMIN_LOG_CHANNEL_ID, text=text)
    except Exception:
        return


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to TNNR Giveaways.\n\n"
        "If you are owed accounts, I will deliver them automatically here in DM.\n"
        "You can run /start any time to check for pending accounts."
    )
    if getattr(update.effective_chat, "type", None) == "private":
        await _reply_with_direct_delivery(update, context, trigger="start")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "TNNR Bot Help\n\n"
        "User Commands:\n\n"
        "/start\n"
        "Check for and automatically receive any pending accounts.\n\n"
        "/bonus\n"
        "Claim one free bonus account every 5 days when stock is available.\n\n"
        "/mycodes\n"
        "Legacy command: claim codes are no longer required; this checks your pending direct-delivery balance.\n\n"
        "/claimcode CPM-XXXXX\n"
        "Legacy command: claim codes are deprecated. Open DM or run /start to receive pending accounts.\n\n"
        "Admin Commands:\n\n"
        "/give TELEGRAM_ID AMOUNT\n"
        "Assign direct owed accounts to a Telegram user."
    )



async def _send_bonus_admin_log(context: ContextTypes.DEFAULT_TYPE, user, account: str, remaining: int, claimed_at: str):
    if not ADMIN_LOG_CHANNEL_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=ADMIN_LOG_CHANNEL_ID,
            text=(
                "🎁 Bonus account claimed\n\n"
                f"User Telegram ID: {user.id}\n"
                f"Username: @{user.username if user.username else 'None'}\n"
                f"Time claimed: {claimed_at}Z\n"
                f"Account sent: {account}\n"
                f"Remaining accounts in pool: {remaining}"
            ),
        )
    except Exception:
        return


async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Public /bonus command: DM exactly one account with a 120-hour cooldown."""
    user = update.effective_user
    if not user or not update.message:
        return
    username = user.username or getattr(user, "full_name", None)
    reservation = bonus_service.begin_claim(user.id, username)

    if reservation.get("status") == "cooldown":
        await update.message.reply_text(
            f"⏳ You already claimed a bonus account. You can use /bonus again in {reservation.get('remaining_text')}."
        )
        return
    if reservation.get("status") == "in_progress":
        await update.message.reply_text(reservation.get("message", "Your bonus claim is already being processed. Please wait a moment."))
        return
    if reservation.get("status") == "no_stock":
        await update.message.reply_text("No bonus accounts are available right now. Please try again later.")
        return
    if reservation.get("status") != "reserved":
        await update.message.reply_text("❌ Bonus claim could not be completed right now. Please try again later.")
        return

    account = reservation["account"]
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                "🎁 Bonus Account\n\n"
                "Here is your bonus account:\n"
                f"{account}\n\n"
                "You can claim another bonus in 5 days. Please save these credentials immediately."
            ),
        )
    except Exception:
        bonus_service.fail_claim(reservation["claim_id"], user.id, reservation["account_id"], "DM_FAILED")
        await update.message.reply_text("Please start the bot in DMs first, then rerun /bonus.")
        return

    completed = bonus_service.complete_claim(
        reservation["claim_id"], user.id, reservation["account_id"], account, username
    )
    if completed.get("status") != "delivered":
        await update.message.reply_text("❌ Bonus claim could not be finalized. Please contact an admin.")
        return

    await _send_bonus_admin_log(context, user, account, completed.get("remaining", 0), completed.get("claimed_at"))
    if getattr(update.effective_chat, "type", None) != "private":
        await update.message.reply_text("✅ Bonus account sent to your DM.")


async def mycodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backward-compatible lookup for the new direct-delivery system."""
    user = update.effective_user
    pending = direct_delivery_service.get_pending_amount(user.id)
    if pending <= 0:
        await update.message.reply_text(
            "🎟️ My Pending Accounts\n\n"
            "You do not currently have any pending accounts.\n\n"
            "If you recently won, make sure you are using the same Telegram account that entered the giveaway."
        )
        return
    await update.message.reply_text(
        "🎟️ My Pending Accounts\n\n"
        f"You have {pending} pending account(s).\n\n"
        "Claim codes are no longer required. Run /start or send me any DM to receive them automatically."
    )


async def private_delivery_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deliver owed accounts when a normal user DMs the bot."""
    if not update.message or not update.effective_user:
        return
    if getattr(update.effective_chat, "type", None) != "private":
        return
    await _reply_with_direct_delivery(update, context, trigger="private_dm")


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


def _extract_claim_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Extract the full user-entered code from a Telegram command message.

    PTB context.args is whitespace-split, which is fine for simple inputs but
    loses the original shape of pasted codes with newlines, bot-name command
    suffixes, and unusual spacing.  Prefer the raw message text after the first
    command token, then fall back to joined args for test doubles/edge cases.
    """
    text = getattr(getattr(update, "message", None), "text", None) or ""
    if text.strip():
        parts = text.strip().split(maxsplit=1)
        if len(parts) > 1 and parts[0].lower().startswith("/claimcode"):
            return parts[1].strip()
    return " ".join(getattr(context, "args", []) or []).strip()


async def claimcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backward-compatible claim-code command. Direct delivery is primary."""
    chat = update.effective_chat
    if getattr(chat, "type", None) != "private":
        await update.message.reply_text("❌ Account delivery can only be handled in a private DM with the bot.")
        return
    raw_code = _extract_claim_code_input(update, context)
    user = update.effective_user
    if not raw_code:
        await update.message.reply_text(
            "Claim codes are no longer required. I will check your Telegram ID for pending accounts now."
        )
        await _reply_with_direct_delivery(update, context, trigger="legacy_claimcode")
        return

    # Legacy compatibility: old unredeemed codes can still be redeemed, but
    # new winner/admin flows allocate direct owed balances instead.
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
    application.add_handler(CommandHandler("bonus", bonus))
    application.add_handler(CommandHandler("mycodes", mycodes))
    application.add_handler(CommandHandler("claimcode", claimcode))
    application.add_handler(CommandHandler("admin_upload_pool", admin_upload_pool))
    application.add_handler(CommandHandler("pool_add_single", pool_add_single))
    application.add_handler(CommandHandler("pool_mark_invalid", pool_mark_invalid))
    application.add_handler(MessageHandler(filters.Document.ALL, receive_pool_file))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_delivery_check))
