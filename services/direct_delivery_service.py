"""Direct owed-account allocation and delivery service.

This replaces the user-facing claim-code redemption requirement.  Owed
accounts are keyed by Telegram user ID and delivered atomically from the
existing account_pool table when a user opens/DMs the bot.
"""

import logging
import re
from datetime import datetime

from database.database import db
from utils.audit_logger import audit_log

logger = logging.getLogger("tnnr.services.direct_delivery")

PENDING_STATUSES = ("pending", "partial")


def parse_account_amount(prize_text: str) -> int | None:
    """Parse a safe account quantity from giveaway/admin prize text.

    Existing commands store free-form prize text.  The safe rule is: use the
    first positive integer in the text; if the text clearly says a singular
    account with no number, treat it as 1.  Otherwise return None so the bot
    does not silently deliver the wrong quantity.
    """
    text = prize_text or ""
    match = re.search(r"\b(\d+)\b", text)
    if match:
        amount = int(match.group(1))
        return amount if amount > 0 else None
    if re.search(r"\baccount\b", text, re.IGNORECASE):
        return 1
    return None


class DirectDeliveryService:
    """Persistent owed-balance allocation and account delivery."""

    @staticmethod
    def allocate_owed_accounts(telegram_id: int, amount: int, source_type: str = "admin_give",
                               giveaway_id: str | None = None, prize: str | None = None,
                               created_by_admin_id: int | None = None,
                               actor_name: str | None = None) -> dict:
        if not isinstance(telegram_id, int) or telegram_id <= 0:
            return {"success": False, "message": "Invalid Telegram ID"}
        if not isinstance(amount, int) or amount <= 0:
            return {"success": False, "message": "Amount must be a positive integer"}
        try:
            now = datetime.utcnow().isoformat()
            db.execute(
                """INSERT INTO account_entitlements
                   (telegram_id, owed_amount, delivered_amount, status, source_type, giveaway_id,
                    prize, created_by_admin_id, created_at, updated_at)
                   VALUES (?, ?, 0, 'pending', ?, ?, ?, ?, ?, ?)""",
                (telegram_id, amount, source_type, giveaway_id, prize, created_by_admin_id, now, now),
            )
            db.commit()
            audit_log.log(
                action="ACCOUNT_OWED_ALLOCATED",
                actor_id=created_by_admin_id,
                actor_name=actor_name,
                details=f"Telegram ID: {telegram_id}, Amount: {amount}, Source: {source_type}, Giveaway: {giveaway_id}, Prize: {prize}",
                result="SUCCESS",
            )
            return {"success": True, "telegram_id": telegram_id, "amount": amount, "pending": DirectDeliveryService.get_pending_amount(telegram_id)}
        except Exception as exc:
            logger.error("Failed to allocate owed accounts: %s", exc)
            db.rollback()
            return {"success": False, "message": str(exc)}

    @staticmethod
    def admin_give(admin_id: int, admin_name: str, telegram_id: int, amount: int) -> dict:
        result = DirectDeliveryService.allocate_owed_accounts(
            telegram_id=telegram_id, amount=amount, source_type="admin_give",
            created_by_admin_id=admin_id, actor_name=admin_name, prize=f"{amount} Account{'s' if amount != 1 else ''}",
        )
        if result.get("success"):
            audit_log.log(
                action="ADMIN_GIVE",
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Assigned {amount} owed account(s) to Telegram ID {telegram_id}",
                result="SUCCESS",
            )
        return result

    @staticmethod
    def get_pending_entitlements(telegram_id: int) -> list[dict]:
        rows = db.execute_all(
            """SELECT id, telegram_id, owed_amount, delivered_amount, status, source_type, giveaway_id, prize, created_at
               FROM account_entitlements
               WHERE telegram_id = ? AND status IN ('pending', 'partial')
                 AND owed_amount > delivered_amount
               ORDER BY created_at ASC, id ASC""",
            (telegram_id,),
        )
        return [dict(row) for row in rows]

    @staticmethod
    def get_pending_amount(telegram_id: int) -> int:
        row = db.execute_one(
            """SELECT COALESCE(SUM(owed_amount - delivered_amount), 0)
               FROM account_entitlements
               WHERE telegram_id = ? AND status IN ('pending', 'partial')
                 AND owed_amount > delivered_amount""",
            (telegram_id,),
        )
        return int(row[0] or 0) if row else 0

    @staticmethod
    def attempt_delivery_for_user(telegram_id: int, username: str | None = None, trigger: str = "dm") -> dict:
        """Deliver all pending accounts for one user atomically.

        Uses BEGIN IMMEDIATE so concurrent /start/DM attempts serialize at the
        SQLite level.  If stock is insufficient, owed balances are unchanged.
        """
        conn = db.connect()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            pending_rows = conn.execute(
                """SELECT id, owed_amount, delivered_amount, source_type, giveaway_id, prize
                   FROM account_entitlements
                   WHERE telegram_id = ? AND status IN ('pending', 'partial')
                     AND owed_amount > delivered_amount
                   ORDER BY created_at ASC, id ASC""",
                (telegram_id,),
            ).fetchall()
            owed_total = sum(int(row[1]) - int(row[2] or 0) for row in pending_rows)
            if owed_total <= 0:
                conn.commit()
                return {"status": "no_pending", "success": False, "accounts": [], "owed_amount": 0}

            available_count = conn.execute("SELECT COUNT(*) FROM account_pool WHERE status = 'available'").fetchone()[0]
            if available_count < owed_total:
                now = datetime.utcnow().isoformat()
                conn.execute(
                    """INSERT INTO account_delivery_logs
                       (telegram_id, amount_requested, amount_delivered, status, reason, trigger, created_at)
                       VALUES (?, ?, 0, 'failed', 'INSUFFICIENT_STOCK', ?, ?)""",
                    (telegram_id, owed_total, trigger, now),
                )
                conn.commit()
                audit_log.log(
                    action="DIRECT_DELIVERY_FAILED",
                    actor_id=telegram_id,
                    actor_name=username,
                    details=f"Requested: {owed_total}, Available: {available_count}, Reason: INSUFFICIENT_STOCK",
                    result="FAILED",
                )
                return {
                    "status": "insufficient_stock", "success": False, "accounts": [],
                    "owed_amount": owed_total, "available": available_count,
                    "message": "You have pending accounts, but stock is temporarily unavailable. Please try again later.",
                }

            accounts = conn.execute(
                "SELECT id, email, password FROM account_pool WHERE status = 'available' ORDER BY id ASC LIMIT ?",
                (owed_total,),
            ).fetchall()
            now = datetime.utcnow().isoformat()
            delivery_ref = f"DIRECT-{telegram_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
            account_ids = [row[0] for row in accounts]
            for account_id in account_ids:
                conn.execute(
                    """UPDATE account_pool
                       SET status = 'delivered', assigned_user = ?, assigned_claim_code = ?, delivered_at = ?
                       WHERE id = ? AND status = 'available'""",
                    (telegram_id, delivery_ref, now, account_id),
                )

            remaining = owed_total
            entitlement_ids = []
            for row in pending_rows:
                entitlement_id = row[0]
                row_remaining = int(row[1]) - int(row[2] or 0)
                deliver_now = min(row_remaining, remaining)
                if deliver_now <= 0:
                    continue
                new_delivered = int(row[2] or 0) + deliver_now
                new_status = "fulfilled" if new_delivered >= int(row[1]) else "partial"
                conn.execute(
                    "UPDATE account_entitlements SET delivered_amount = ?, status = ?, updated_at = ? WHERE id = ?",
                    (new_delivered, new_status, now, entitlement_id),
                )
                entitlement_ids.append(str(entitlement_id))
                remaining -= deliver_now
                if remaining <= 0:
                    break

            conn.execute(
                """INSERT INTO account_delivery_logs
                   (telegram_id, amount_requested, amount_delivered, status, reason, trigger, account_ids, entitlement_ids, created_at)
                   VALUES (?, ?, ?, 'delivered', NULL, ?, ?, ?, ?)""",
                (telegram_id, owed_total, len(account_ids), trigger, ",".join(map(str, account_ids)), ",".join(entitlement_ids), now),
            )
            conn.commit()
            formatted_accounts = [f"{row[1]}:{row[2]}" for row in accounts]
            audit_log.log(
                action="DIRECT_ACCOUNTS_DELIVERED",
                actor_id=telegram_id,
                actor_name=username,
                details=f"Delivered: {len(formatted_accounts)}, Trigger: {trigger}, Delivery ref: {delivery_ref}",
                result="SUCCESS",
            )
            return {
                "status": "delivered", "success": True, "accounts": formatted_accounts,
                "accounts_delivered": len(formatted_accounts), "owed_amount": owed_total,
                "delivery_ref": delivery_ref,
            }
        except Exception as exc:
            logger.error("Direct delivery failed for %s: %s", telegram_id, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "success": False, "accounts": [], "message": f"Delivery error: {exc}"}


direct_delivery_service = DirectDeliveryService()
