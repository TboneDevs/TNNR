"""Announcement channel and discussion group validation helpers."""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from telegram.error import BadRequest, Forbidden

from config import ANNOUNCEMENT_CHANNEL_ID, DISCUSSION_GROUP_ID

logger = logging.getLogger("tnnr.channel")
ANNOUNCEMENT_CHANNEL_USERNAME = "@TnnrCPM"
DISCUSSION_TEST_PHRASE = "test trivia access"

# user_id -> chat_id that should receive the live discussion read-test result.
_DISCUSSION_READ_TEST_TARGETS: Dict[int, int] = {}


@dataclass
class ChannelCheckResult:
    ok: bool
    reason: Optional[str] = None
    details: Optional[str] = None
    title: Optional[str] = None


@dataclass
class AnnouncementPostResult:
    ok: bool
    message_id: Optional[int] = None
    reason: Optional[str] = None
    details: Optional[str] = None


def classify_telegram_error(exc: Exception) -> str:
    """Map Telegram exceptions to announcement-channel error categories."""
    message = str(exc).lower()
    if isinstance(exc, BadRequest) and ("chat not found" in message or "not found" in message):
        return "CHANNEL_NOT_FOUND"
    if isinstance(exc, Forbidden):
        if "not enough rights" in message or "can't" in message or "forbidden" in message:
            return "INSUFFICIENT_PERMISSIONS"
        return "BOT_NOT_ADMIN"
    if "not enough rights" in message or "need administrator" in message or "not an administrator" in message:
        return "INSUFFICIENT_PERMISSIONS"
    if "chat not found" in message or "channel_invalid" in message:
        return "CHANNEL_NOT_FOUND"
    if "forbidden" in message:
        return "BOT_NOT_ADMIN"
    return "TELEGRAM_API_ERROR"


def classify_group_error(exc: Exception) -> str:
    """Map Telegram exceptions to discussion-group error categories."""
    message = str(exc).lower()
    if isinstance(exc, BadRequest) and ("chat not found" in message or "not found" in message):
        return "GROUP_NOT_FOUND"
    if isinstance(exc, Forbidden):
        if "bot was kicked" in message or "not a member" in message or "forbidden" in message:
            return "BOT_NOT_IN_GROUP"
        return "INSUFFICIENT_PERMISSIONS"
    if "chat not found" in message or "group_invalid" in message:
        return "GROUP_NOT_FOUND"
    if "not enough rights" in message or "can't" in message or "not allowed" in message:
        return "INSUFFICIENT_PERMISSIONS"
    if "privacy" in message:
        return "PRIVACY_MODE_BLOCKING_MESSAGES"
    return "TELEGRAM_API_ERROR"


def _member_can_post(member) -> bool:
    status = getattr(member, "status", None)
    if status == "creator":
        return True
    if status != "administrator":
        return False
    return bool(getattr(member, "can_post_messages", False))


def _member_can_send(member) -> bool:
    status = getattr(member, "status", None)
    if status in {"creator", "administrator", "member"}:
        return True
    if status == "restricted":
        return bool(getattr(member, "can_send_messages", False))
    return False


async def verify_announcement_channel(bot) -> ChannelCheckResult:
    """Verify the configured announcement channel exists and bot can post there."""
    if not ANNOUNCEMENT_CHANNEL_ID:
        return ChannelCheckResult(False, "CHANNEL_NOT_FOUND", "ANNOUNCEMENT_CHANNEL_ID is not configured")
    try:
        chat = await bot.get_chat(ANNOUNCEMENT_CHANNEL_ID)
        member = await bot.get_chat_member(chat.id, bot.id)
        if not _member_can_post(member):
            return ChannelCheckResult(
                False,
                "BOT_NOT_ADMIN" if getattr(member, "status", None) not in {"administrator", "creator"} else "INSUFFICIENT_PERMISSIONS",
                f"Bot status={getattr(member, 'status', 'unknown')}; can_post_messages={getattr(member, 'can_post_messages', None)}",
                getattr(chat, "title", None),
            )
        return ChannelCheckResult(True, title=getattr(chat, "title", None))
    except Exception as exc:
        reason = classify_telegram_error(exc)
        logger.error("Announcement channel verification failed: %s: %s", reason, exc)
        return ChannelCheckResult(False, reason, str(exc))


async def verify_discussion_group(bot) -> ChannelCheckResult:
    """Verify the configured discussion group exists and the bot can participate.

    Telegram has no API flag proving privacy-mode read behavior for normal group
    messages, so read access is confirmed by the live /discussiontest phrase.
    """
    if not DISCUSSION_GROUP_ID:
        return ChannelCheckResult(False, "GROUP_NOT_FOUND", "DISCUSSION_GROUP_ID is not configured")
    try:
        chat = await bot.get_chat(DISCUSSION_GROUP_ID)
        member = await bot.get_chat_member(chat.id, bot.id)
        if not _member_can_send(member):
            status = getattr(member, "status", None)
            reason = "BOT_NOT_IN_GROUP" if status in {"left", "kicked"} else "INSUFFICIENT_PERMISSIONS"
            return ChannelCheckResult(
                False,
                reason,
                f"Bot status={status}; can_send_messages={getattr(member, 'can_send_messages', None)}",
                getattr(chat, "title", None),
            )
        return ChannelCheckResult(True, title=getattr(chat, "title", None))
    except Exception as exc:
        reason = classify_group_error(exc)
        logger.error("Discussion group verification failed: %s: %s", reason, exc)
        return ChannelCheckResult(False, reason, str(exc))


async def post_announcement(bot, text: str) -> AnnouncementPostResult:
    """Validate then post a giveaway announcement to the configured channel."""
    check = await verify_announcement_channel(bot)
    if not check.ok:
        return AnnouncementPostResult(False, reason=check.reason, details=check.details)
    try:
        message = await bot.send_message(chat_id=ANNOUNCEMENT_CHANNEL_ID, text=text)
        return AnnouncementPostResult(True, message_id=message.message_id)
    except Exception as exc:
        reason = classify_telegram_error(exc)
        logger.error("Announcement post failed: %s: %s", reason, exc)
        return AnnouncementPostResult(False, reason=reason, details=str(exc))


def start_discussion_read_test(admin_user_id: int, notify_chat_id: int):
    _DISCUSSION_READ_TEST_TARGETS[int(admin_user_id)] = int(notify_chat_id)


def get_discussion_read_targets() -> Dict[int, int]:
    return dict(_DISCUSSION_READ_TEST_TARGETS)


def clear_discussion_read_test(admin_user_id: int):
    _DISCUSSION_READ_TEST_TARGETS.pop(int(admin_user_id), None)
