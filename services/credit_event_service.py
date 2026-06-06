"""Persistent promotional credit event creation and one-time user claims."""

import logging
from datetime import datetime

from database.database import db
from services.direct_delivery_service import DirectDeliveryService
from utils.audit_logger import audit_log

logger = logging.getLogger("tnnr.services.credit_event")
EVENT_CREDIT_AMOUNT = 3


def _now() -> str:
    return datetime.utcnow().isoformat()


class CreditEventService:
    @staticmethod
    def create_event(admin_id: int, admin_name: str | None, announcement_channel_id: int, announcement_message_id: int) -> dict:
        conn = db.connect()
        now = _now()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE credit_events SET status = 'closed', updated_at = ? WHERE status = 'active'", (now,))
            cursor = conn.execute(
                """INSERT INTO credit_events
                   (created_by_admin_id, admin_name, announcement_channel_id, announcement_message_id, credit_amount, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (admin_id, admin_name, announcement_channel_id, announcement_message_id, EVENT_CREDIT_AMOUNT, now, now),
            )
            event_id = cursor.lastrowid
            conn.commit()
            audit_log.log(
                action="CREDIT_EVENT_CREATED",
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Event ID: {event_id}, Credits: {EVENT_CREDIT_AMOUNT}, Announcement message: {announcement_message_id}",
                result="SUCCESS",
            )
            return {"success": True, "event_id": event_id, "credit_amount": EVENT_CREDIT_AMOUNT}
        except Exception as exc:
            logger.error("Failed to create credit event: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    @staticmethod
    def get_current_event() -> dict | None:
        row = db.execute_one(
            """SELECT id, credit_amount, created_at, announcement_message_id
               FROM credit_events WHERE status = 'active'
               ORDER BY id DESC LIMIT 1"""
        )
        return dict(row) if row else None

    @staticmethod
    def claim_current_event(telegram_id: int, username: str | None = None, display_name: str | None = None) -> dict:
        conn = db.connect()
        now = _now()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute(
                """SELECT id, credit_amount FROM credit_events
                   WHERE status = 'active' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            if not event:
                conn.commit()
                return {"success": False, "status": "no_event", "message": "No credit event is active right now."}
            event_id, amount = int(event[0]), int(event[1] or EVENT_CREDIT_AMOUNT)
            existing = conn.execute(
                "SELECT id FROM credit_event_claims WHERE event_id = ? AND telegram_id = ? LIMIT 1",
                (event_id, telegram_id),
            ).fetchone()
            if existing:
                conn.commit()
                return {"success": False, "status": "already_claimed", "event_id": event_id}

            DirectDeliveryService._ensure_profile(conn, telegram_id, username, display_name)
            conn.execute(
                """INSERT INTO account_entitlements
                   (telegram_id, owed_amount, delivered_amount, status, source_type, prize, credit_type, created_at, updated_at)
                   VALUES (?, ?, 0, 'pending', 'credit_event', ?, 'promotional', ?, ?)""",
                (telegram_id, amount, f"{amount} Promotional Event Credits", now, now),
            )
            withdrawable_row = conn.execute(
                """SELECT COALESCE(SUM(owed_amount - delivered_amount), 0)
                   FROM account_entitlements
                   WHERE telegram_id = ? AND credit_type = 'withdrawable'
                     AND status IN ('pending', 'partial') AND owed_amount > delivered_amount""",
                (telegram_id,),
            ).fetchone()
            promo_row = conn.execute(
                """SELECT COALESCE(SUM(owed_amount - delivered_amount), 0)
                   FROM account_entitlements
                   WHERE telegram_id = ? AND credit_type = 'promotional'
                     AND status IN ('pending', 'partial') AND owed_amount > delivered_amount""",
                (telegram_id,),
            ).fetchone()
            new_balance = int(withdrawable_row[0] or 0)
            promotional_balance = int(promo_row[0] or 0)
            conn.execute(
                """INSERT INTO credit_event_claims
                   (event_id, telegram_id, username, credits_awarded, new_balance, claimed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, telegram_id, username, amount, new_balance, now),
            )
            conn.commit()
            audit_log.log(
                action="CREDIT_EVENT_CLAIMED",
                actor_id=telegram_id,
                actor_name=username,
                details=f"Event ID: {event_id}, Promotional credits: {amount}, Withdrawable balance: {new_balance}, Promotional balance: {promotional_balance}",
                result="SUCCESS",
            )
            return {"success": True, "status": "claimed", "event_id": event_id, "amount": amount, "balance": new_balance, "withdrawable_balance": new_balance, "promotional_balance": promotional_balance, "claimed_at": now}
        except Exception as exc:
            logger.error("Failed to claim credit event: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            # Unique constraint can race; convert it to the expected duplicate message.
            if "UNIQUE" in str(exc).upper():
                return {"success": False, "status": "already_claimed"}
            return {"success": False, "status": "error", "message": str(exc)}


credit_event_service = CreditEventService()
