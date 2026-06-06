"""Telegram claim, user, bonus, and pool handlers."""

from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from config import ADMIN_LOG_CHANNEL_ID
from services.bonus_service import bonus_service
from services.credit_event_service import credit_event_service
from services.claim_service import claim_service
from services.direct_delivery_service import (
    CLAIM_DM_FAILURE_MESSAGE,
    NO_UNCLAIMED_MESSAGE,
    OUT_OF_CREDITS_MESSAGE,
    PROMOTIONAL_WITHDRAW_MESSAGE,
    SLOTS_NOTE,
    direct_delivery_service,
)
from services.pool_service import pool_service
from utils.permissions import is_admin
from utils.privacy import PUBLIC_ACCOUNT_PRIVACY_MESSAGE, START_DM_FIRST_MESSAGE, is_private_chat

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


def _username(user) -> str | None:
    return user.username or getattr(user, "full_name", None)


async def _send_admin_log(context: ContextTypes.DEFAULT_TYPE, text: str):
    if not ADMIN_LOG_CHANNEL_ID:
        return
    try:
        await context.bot.send_message(chat_id=ADMIN_LOG_CHANNEL_ID, text=text)
    except Exception:
        return


async def _send_claim_log(context: ContextTypes.DEFAULT_TYPE, user, command: str, result: dict, status: str):
    if status == "success":
        text = (
            "✅ Account credits claimed\n\n"
            f"User Telegram ID: {user.id}\n"
            f"Username: @{user.username if user.username else 'None'}\n"
            f"Command used: /{command}\n"
            f"Accounts sent: {result.get('accounts_delivered')}\n"
            f"Withdrawable balance: {result.get('withdrawable_balance', result.get('balance'))}\n"
            f"Promotional balance: {result.get('promotional_balance')}\n"
            f"Remaining account pool count: {result.get('remaining_pool')}\n"
            f"Time: {datetime.utcnow().isoformat()}Z"
        )
    else:
        text = (
            "⚠️ Account credit claim failed\n\n"
            f"User Telegram ID: {user.id}\n"
            f"Username: @{user.username if user.username else 'None'}\n"
            f"Command used: /{command}\n"
            f"Reason: {result.get('status') or result.get('message')}\n"
            f"Pending balance: {result.get('owed_amount')}\n"
            f"Available stock: {result.get('available')}\n"
            f"Time: {datetime.utcnow().isoformat()}Z"
        )
    await _send_admin_log(context, text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    summary = direct_delivery_service.get_balance_summary(user.id, _username(user), getattr(user, "full_name", None)) if user else {"balance": 0}
    balance = summary.get("balance", 0)
    balance_line = f"You currently have {balance} unclaimed account credit(s)." if balance else "You currently have no unclaimed accounts available."
    await update.message.reply_text(
        "👋 Welcome to TNNR Giveaways.\n\n"
        "Start the bot in DMs, then run /claim to claim your accounts.\n"
        f"{balance_line}\n\n"
        "Use /balance to view withdrawable and promotional credits. Promotional event credits can be used for /slots or /coinflip; only winnings become withdrawable for /claim."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "TNNR Bot Help\n\n"
        "Free credit system only: credits come from giveaways, bonuses, or admin-issued rewards. There are no deposits, purchases, or paid gambling credits.\n\n"
        "User Commands:\n\n"
        "/start\n"
        "Open the bot and see claim instructions.\n\n"
        "/balance\n"
        "View your unclaimed account credit balance.\n\n"
        "/claim\n"
        "Convert all available unclaimed credits into account credentials by DM.\n\n"
        "/withdraw\n"
        "Same as /claim.\n\n"
        "/bonus\n"
        "Receive 1 free unclaimed account credit every 5 days.\n\n"
        "/eventclaim\n"
        "Claim the current admin-posted event top-up of 3 promotional credits. Use in DM only.\n\n"
        "/slots\n"
        "Use exactly 1 free unclaimed credit for one slots spin.\n\n"
        "/coinflip heads\n"
        "/coinflip tails\n"
        "Use exactly 1 free unclaimed credit for a 40% win chance.\n\n"
        "/bet 1\n"
        "Store a preferred bet amount for future games; current games still cost exactly 1 credit.\n\n"
        "/leaderboard\n"
        "Show top free-credit balances and totals without account details.\n\n"
        "Admin Commands:\n\n"
        "/give TELEGRAM_ID AMOUNT\n"
        "Assign free unclaimed account credits to a Telegram user."
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    summary = direct_delivery_service.get_balance_summary(user.id, _username(user), getattr(user, "full_name", None))
    await update.message.reply_text(
        f"💰 Balance\n"
        f"Promotional Credits (non-withdrawable): {summary['promotional_balance']}\n"
        f"Withdrawable Credits: {summary['withdrawable_balance']}\n"
        f"Total accounts won: {summary['total_won']}\n"
        f"Total accounts claimed: {summary['total_claimed']}\n"
        f"Available playable credits: {summary['playable_balance']}\n\n"
        "Promotional credits can be used for /slots or /coinflip. Only gambling winnings become withdrawable credits. Use /claim to claim withdrawable credits."
    )

async def bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /bet 1")
        return
    amount = int(context.args[0])
    if amount <= 0:
        await update.message.reply_text("Usage: /bet 1")
        return
    user = update.effective_user
    result = direct_delivery_service.set_current_bet(user.id, amount, _username(user), getattr(user, "full_name", None))
    if not result.get("success"):
        await update.message.reply_text(result.get("message", "Usage: /bet 1"))
        return
    await update.message.reply_text(f"✅ Bet amount saved: {amount} credit(s). Playable balance: {result.get('balance')} credits. Promotional credits do not become withdrawable until they win in a game.")


async def _log_game(context, user, command: str, result: dict):
    await _send_admin_log(
        context,
        (
            f"🎮 Free credit game result\n\n"
            f"User Telegram ID: {user.id}\n"
            f"Username: @{user.username if user.username else 'None'}\n"
            f"Command used: /{command}\n"
            f"Amount wagered: 1\n"
            f"Amount won/lost: {result}\n"
            f"Withdrawable balance: {result.get('withdrawable_balance', result.get('balance'))}\n"
            f"Promotional balance: {result.get('promotional_balance')}\n"
            f"Time: {datetime.utcnow().isoformat()}Z"
        ),
    )


async def slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    result = direct_delivery_service.play_slots(user.id, _username(user))
    if not result.get("success"):
        await update.message.reply_text(result.get("message", OUT_OF_CREDITS_MESSAGE))
        return
    await _log_game(context, user, "slots", result)
    balance_lines = (
        f"Withdrawable balance: {result.get('withdrawable_balance', result.get('balance'))} credits.\n"
        f"Promotional balance: {result.get('promotional_balance', 0)} credits.\n"
    )
    if result.get("won", 0) <= 0:
        await update.message.reply_text(
            f"🎰 Slots Result: ❌ You lost 1 credit.\n"
            f"{balance_lines}"
            f"{SLOTS_NOTE}"
        )
    else:
        await update.message.reply_text(
            f"🎰 Slots Result: 💎 You won {result.get('won')} withdrawable credits!\n"
            f"{balance_lines}"
            f"{SLOTS_NOTE}"
        )

async def coinflip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1 or context.args[0].lower() not in {"heads", "tails"}:
        await update.message.reply_text("Usage: /coinflip heads OR /coinflip tails")
        return
    user = update.effective_user
    result = direct_delivery_service.play_coinflip(user.id, context.args[0], _username(user))
    if not result.get("success"):
        await update.message.reply_text(result.get("message", OUT_OF_CREDITS_MESSAGE))
        return
    await _log_game(context, user, "coinflip", result)
    if result.get("won"):
        await update.message.reply_text(
            f"🪙 Coinflip Result: ✅ You won {result.get('payout', 1)} withdrawable credit{'s' if result.get('payout', 1) != 1 else ''}!\n"
            f"Withdrawable balance: {result.get('withdrawable_balance', result.get('balance'))} credits.\n"
            f"Promotional balance: {result.get('promotional_balance', 0)} credits."
        )
    else:
        await update.message.reply_text(
            f"🪙 Coinflip Result: ❌ You lost 1 credit.\n"
            f"Withdrawable balance: {result.get('withdrawable_balance', result.get('balance'))} credits.\n"
            f"Promotional balance: {result.get('promotional_balance', 0)} credits."
        )

async def _claim_or_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str):
    user = update.effective_user
    invoked_private = is_private_chat(update)
    prepared = direct_delivery_service.prepare_claim_for_user(user.id, _username(user), trigger=command)
    if prepared.get("status") == "promotional_only":
        await update.message.reply_text(PROMOTIONAL_WITHDRAW_MESSAGE)
        return
    if prepared.get("status") == "no_pending":
        await update.message.reply_text(NO_UNCLAIMED_MESSAGE)
        return
    if prepared.get("status") == "insufficient_stock":
        await update.message.reply_text("Stock is low right now. Your unclaimed balance was not reduced. Please try again later.")
        await _send_claim_log(context, user, command, prepared, "failed")
        return
    if prepared.get("status") != "reserved":
        await update.message.reply_text("❌ Claim could not be prepared right now. Please try again later.")
        return

    lines = [
        "✅ Accounts Claimed",
        f"Accounts delivered: {prepared.get('reserved_amount')}",
        "",
        "Your Accounts:",
    ]
    lines.extend(f"{idx}. {account}" for idx, account in enumerate(prepared.get("accounts", []), start=1))
    lines.append("")
    lines.append("Please save these credentials immediately.")
    try:
        await context.bot.send_message(chat_id=user.id, text="\n".join(lines))
    except Exception:
        direct_delivery_service.fail_prepared_claim(
            user.id, prepared["delivery_log_id"], prepared["account_ids"], "DM_FAILED", _username(user), command
        )
        await update.message.reply_text(CLAIM_DM_FAILURE_MESSAGE if invoked_private else START_DM_FIRST_MESSAGE)
        failed = dict(prepared)
        failed["status"] = "dm_failed"
        await _send_claim_log(context, user, command, failed, "failed")
        return

    completed = direct_delivery_service.complete_prepared_claim(
        user.id, prepared["delivery_log_id"], prepared["account_ids"], prepared["reserved_amount"], _username(user), command
    )
    completed["accounts_delivered"] = prepared.get("reserved_amount")
    if not completed.get("success"):
        await update.message.reply_text("❌ Claim could not be finalized. Please contact an admin.")
        return
    await _send_claim_log(context, user, command, completed, "success")
    if not invoked_private:
        await update.message.reply_text(f"✅ {prepared.get('reserved_amount')} account(s) sent to your DM.")
    else:
        extra = ""
        if prepared.get("partial"):
            extra = f"\n\nOnly {prepared.get('reserved_amount')} account(s) were available. Your remaining balance is {completed.get('balance')} credit(s)."
        await update.message.reply_text(f"✅ Claim complete. New balance: {completed.get('balance')} credit(s).{extra}")


async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _claim_or_withdraw(update, context, "claim")


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _claim_or_withdraw(update, context, "withdraw")


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = direct_delivery_service.get_leaderboard(5)

    def fmt_user(row):
        return f"@{row['username']}" if row.get("username") else str(row.get("telegram_id"))

    lines = ["🏆 Free Credit Leaderboard", "", "Current Unclaimed Balance:"]
    for idx, row in enumerate(data["balance"], start=1):
        lines.append(f"{idx}. {fmt_user(row)} — {int(row.get('balance') or 0)} withdrawable / {int(row.get('promotional_balance') or 0)} promotional")
    lines.extend(["", "Total Credits Won:"])
    for idx, row in enumerate(data["total_won"], start=1):
        lines.append(f"{idx}. {fmt_user(row)} — {int(row.get('total_won') or 0)} won")
    lines.extend(["", "Total Accounts Claimed:"])
    for idx, row in enumerate(data["total_claimed"], start=1):
        lines.append(f"{idx}. {fmt_user(row)} — {int(row.get('total_claimed') or 0)} claimed")
    await update.message.reply_text("\n".join(lines))


async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Public /bonus command: award one free unclaimed credit every 120 hours."""
    user = update.effective_user
    if not user or not update.message:
        return
    result = bonus_service.claim_credit(user.id, _username(user))
    if result.get("status") == "cooldown":
        await update.message.reply_text(f"⏳ You already claimed a bonus credit. You can use /bonus again in {result.get('remaining_text')}.")
        return
    if result.get("status") == "in_progress":
        await update.message.reply_text(result.get("message", "Your bonus claim is already being processed. Please wait a moment."))
        return
    if result.get("status") != "awarded":
        await update.message.reply_text("❌ Bonus credit could not be completed right now. Please try again later.")
        return
    await _send_admin_log(
        context,
        (
            "🎁 Bonus credit claimed\n\n"
            f"User Telegram ID: {user.id}\n"
            f"Username: @{user.username if user.username else 'None'}\n"
            f"Time claimed: {result.get('claimed_at')}Z\n"
            "Credit awarded: 1\n"
            f"New balance: {result.get('balance')}"
        ),
    )
    await update.message.reply_text(
        f"✅ Bonus credit added. New balance: {result.get('balance')} credit(s).\n\n"
        "Use /slots or /coinflip to play with free credits, or run /claim to claim accounts."
    )


async def eventclaim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim the active admin credit event once per event."""
    if not update.message or not update.effective_user:
        return
    if not is_private_chat(update):
        await update.message.reply_text(
            "For privacy, event credits can only be claimed in DMs. Please message the bot directly."
        )
        return
    user = update.effective_user
    result = credit_event_service.claim_current_event(user.id, _username(user), getattr(user, "full_name", None))
    if result.get("status") == "already_claimed":
        await update.message.reply_text("You have already claimed this event credit top-up.")
        return
    if not result.get("success"):
        await update.message.reply_text(result.get("message", "No credit event is active right now."))
        return
    await _send_admin_log(
        context,
        (
            "🎟️ Credit event claimed\n\n"
            f"User Telegram ID: {user.id}\n"
            f"Username: @{user.username if user.username else 'None'}\n"
            f"Event ID: {result.get('event_id')}\n"
            f"Credits added: {result.get('amount')}\n"
            f"Withdrawable balance: {result.get('withdrawable_balance', result.get('balance'))}\n"
            f"Promotional balance: {result.get('promotional_balance')}\n"
            f"Time: {datetime.utcnow().isoformat()}Z"
        ),
    )
    await update.message.reply_text("Success! You received 3 event credits.")


async def mycodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backward-compatible guidance for the new free-credit system."""
    user = update.effective_user
    summary = direct_delivery_service.get_balance_summary(user.id, _username(user), getattr(user, "full_name", None))
    if summary["balance"] <= 0:
        await update.message.reply_text(
            "🎟️ My Pending Accounts\n\n"
            "You do not currently have any unclaimed account credits.\n\n"
            "If you recently won, make sure you are using the same Telegram account that entered the giveaway."
        )
        return
    await update.message.reply_text(
        "🎟️ My Pending Accounts\n\n"
        f"You have {summary['balance']} unclaimed account credit(s).\n\n"
        "Claim codes are no longer required. Run /claim or /withdraw in DM to receive account credentials."
    )


async def private_delivery_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Do not auto-deliver accounts on arbitrary DMs; give claim guidance."""
    if not update.message or not update.effective_user:
        return
    if getattr(update.effective_chat, "type", None) != "private":
        return
    user = update.effective_user
    balance_amount = direct_delivery_service.get_pending_amount(user.id)
    if balance_amount > 0:
        await update.message.reply_text(
            f"You have {balance_amount} unclaimed account credit(s). Run /claim to claim your accounts."
        )
    else:
        await update.message.reply_text("You have no unclaimed accounts available.")


async def _send_redemption_admin_log(context: ContextTypes.DEFAULT_TYPE, user, result: dict):
    await _send_admin_log(
        context,
        (
            "✅ Legacy claim code redeemed\n\n"
            f"Claim code: {result.get('claim_code')}\n"
            f"Telegram ID: {user.id}\n"
            f"Username: @{user.username if user.username else 'None'}\n"
            f"Prize: {result.get('prize')}\n"
            f"Accounts delivered: {result.get('accounts_delivered')}\n"
            f"Timestamp: {datetime.utcnow().isoformat()}Z"
        ),
    )


def _extract_claim_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    text = getattr(getattr(update, "message", None), "text", None) or ""
    if text.strip():
        parts = text.strip().split(maxsplit=1)
        if len(parts) > 1 and parts[0].lower().startswith("/claimcode"):
            return parts[1].strip()
    return " ".join(getattr(context, "args", []) or []).strip()


async def claimcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legacy compatibility command. New users should use /claim."""
    chat = update.effective_chat
    if not is_private_chat(update):
        await update.message.reply_text(PUBLIC_ACCOUNT_PRIVACY_MESSAGE)
        return
    raw_code = _extract_claim_code_input(update, context)
    user = update.effective_user
    if not raw_code:
        await update.message.reply_text("Claim codes are no longer required. Run /claim to claim any unclaimed account credits.")
        return
    result = claim_service.redeem_claim_code(raw_code, user.id, _username(user))
    if not result["success"]:
        await update.message.reply_text(result["message"])
        return
    lines = ["✅ Prize Delivered Successfully", f"Claim Code: {result.get('claim_code') or raw_code}", "Your Accounts:"]
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
    result = pool_service.import_accounts(lines, user.id, _username(user))
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
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("bet", bet))
    application.add_handler(CommandHandler("slots", slots))
    application.add_handler(CommandHandler("coinflip", coinflip))
    application.add_handler(CommandHandler(["claim", "claims"], claim))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("bonus", bonus))
    application.add_handler(CommandHandler("eventclaim", eventclaim))
    application.add_handler(CommandHandler("mycodes", mycodes))
    application.add_handler(CommandHandler("claimcode", claimcode))
    application.add_handler(CommandHandler("admin_upload_pool", admin_upload_pool))
    application.add_handler(CommandHandler("pool_add_single", pool_add_single))
    application.add_handler(CommandHandler("pool_mark_invalid", pool_mark_invalid))
    application.add_handler(MessageHandler(filters.Document.ALL, receive_pool_file))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_delivery_check))
