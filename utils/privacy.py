"""Chat privacy helpers for account-sensitive bot responses."""

from telegram import Update

PUBLIC_ACCOUNT_PRIVACY_MESSAGE = "For privacy, account details can only be viewed in DMs. Please message the bot directly."
START_DM_FIRST_MESSAGE = "Please start the bot in DMs first, then run the command again."


def is_private_chat(update_or_chat) -> bool:
    """Return True only for Telegram private DM chats."""
    chat = getattr(update_or_chat, "effective_chat", update_or_chat)
    return getattr(chat, "type", None) == "private"


def is_public_chat(update_or_chat) -> bool:
    return not is_private_chat(update_or_chat)
