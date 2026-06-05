"""One-account bonus claim service with persistent cooldowns.

The /bonus flow is intentionally separate from direct owed-account delivery:
bonus claims always deliver exactly one available account, enforce a per-user
120 hour cooldown, and only mark stock delivered after the DM succeeds.
"""

import logging
from datetime import datetime, timedelta

from database.database import db
from utils.audit_logger import audit_log

logger = logging.getLogger("tnnr.services.bonus")

BONUS_COOLDOWN_HOURS = 120
BONUS_COOLDOWN = timedelta(hours=BONUS_COOLDOWN_HOURS)
IN_PROGRESS_STALE_MINUTES = 15


class BonusService:
    """Persistent /bonus cooldown, reservation, and delivery bookkeeping."""

    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    @staticmethod
    def _parse_dt(value) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value).replace("Z", "")
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text.split("+")[0], fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text.split("+")[0])
        except ValueError:
            return None

    @staticmethod
    def _format_remaining(delta: timedelta) -> str:
        total_seconds = max(0, int(delta.total_seconds()))
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes or not parts:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        return ", ".join(parts)

    @staticmethod
    def get_cooldown(telegram_id: int) -> dict:
        row = db.execute_one(
            """SELECT claimed_at FROM bonus_claims
               WHERE telegram_id = ? AND status = 'delivered'
               ORDER BY claimed_at DESC, id DESC LIMIT 1""",
            (telegram_id,),
        )
        last_claim = BonusService._parse_dt(row[0]) if row else None
        if not last_claim:
            return {"active": False, "last_claim": None, "remaining": None, "remaining_text": None}
        expires_at = last_claim + BONUS_COOLDOWN
        now = BonusService._now()
        if now >= expires_at:
            return {"active": False, "last_claim": last_claim, "remaining": timedelta(0), "remaining_text": None}
        remaining = expires_at - now
        return {
            "active": True,
            "last_claim": last_claim,
            "remaining": remaining,
            "remaining_text": BonusService._format_remaining(remaining),
        }

    @staticmethod
    def _release_stale_in_progress(conn, telegram_id: int, now: datetime):
        stale_before = (now - timedelta(minutes=IN_PROGRESS_STALE_MINUTES)).isoformat()
        stale_rows = conn.execute(
            """SELECT id, account_id FROM bonus_claims
               WHERE telegram_id = ? AND status = 'in_progress' AND created_at < ?""",
            (telegram_id, stale_before),
        ).fetchall()
        for claim_id, account_id in stale_rows:
            if account_id:
                conn.execute(
                    """UPDATE account_pool
                       SET status = 'available', reserved_at = NULL, assigned_user = NULL, assigned_claim_code = NULL
                       WHERE id = ? AND status = 'reserved'""",
                    (account_id,),
                )
            conn.execute(
                """UPDATE bonus_claims
                   SET status = 'failed', failure_reason = 'STALE_IN_PROGRESS_RELEASED', failed_at = ?, updated_at = ?
                   WHERE id = ?""",
                (now.isoformat(), now.isoformat(), claim_id),
            )

    @staticmethod
    def begin_claim(telegram_id: int, username: str | None = None) -> dict:
        """Check cooldown/stock and reserve exactly one account for DM delivery."""
        conn = db.connect()
        now = BonusService._now()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            BonusService._release_stale_in_progress(conn, telegram_id, now)

            last_row = conn.execute(
                """SELECT claimed_at FROM bonus_claims
                   WHERE telegram_id = ? AND status = 'delivered'
                   ORDER BY claimed_at DESC, id DESC LIMIT 1""",
                (telegram_id,),
            ).fetchone()
            last_claim = BonusService._parse_dt(last_row[0]) if last_row else None
            if last_claim and now < last_claim + BONUS_COOLDOWN:
                remaining = (last_claim + BONUS_COOLDOWN) - now
                conn.commit()
                return {"status": "cooldown", "remaining_text": BonusService._format_remaining(remaining)}

            active = conn.execute(
                "SELECT id FROM bonus_claims WHERE telegram_id = ? AND status = 'in_progress' LIMIT 1",
                (telegram_id,),
            ).fetchone()
            if active:
                conn.commit()
                return {"status": "in_progress", "message": "Your bonus claim is already being processed. Please wait a moment."}

            account = conn.execute(
                "SELECT id, email, password FROM account_pool WHERE status = 'available' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if not account:
                conn.commit()
                return {"status": "no_stock"}

            account_id, email, password = account[0], account[1], account[2]
            reservation_ref = f"BONUS-{telegram_id}-{now.strftime('%Y%m%d%H%M%S%f')}"
            conn.execute(
                """UPDATE account_pool
                   SET status = 'reserved', reserved_at = ?, assigned_user = ?, assigned_claim_code = ?
                   WHERE id = ? AND status = 'available'""",
                (now.isoformat(), telegram_id, reservation_ref, account_id),
            )
            if conn.total_changes <= 0:
                conn.rollback()
                return {"status": "error", "message": "Could not reserve bonus account."}
            cursor = conn.execute(
                """INSERT INTO bonus_claims
                   (telegram_id, username, account_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'in_progress', ?, ?)""",
                (telegram_id, username, account_id, now.isoformat(), now.isoformat()),
            )
            conn.commit()
            return {
                "status": "reserved",
                "claim_id": cursor.lastrowid,
                "account_id": account_id,
                "account": f"{email}:{password}",
                "reservation_ref": reservation_ref,
            }
        except Exception as exc:
            logger.error("Bonus begin_claim failed for %s: %s", telegram_id, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def complete_claim(claim_id: int, telegram_id: int, account_id: int, account: str, username: str | None = None) -> dict:
        conn = db.connect()
        now = BonusService._now().isoformat()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            claim = conn.execute(
                "SELECT id FROM bonus_claims WHERE id = ? AND telegram_id = ? AND account_id = ? AND status = 'in_progress'",
                (claim_id, telegram_id, account_id),
            ).fetchone()
            if not claim:
                conn.rollback()
                return {"status": "error", "message": "Bonus claim is no longer pending."}
            conn.execute(
                """UPDATE account_pool
                   SET status = 'delivered', delivered_at = ?, assigned_user = ?
                   WHERE id = ? AND status = 'reserved'""",
                (now, telegram_id, account_id),
            )
            conn.execute(
                """UPDATE bonus_claims
                   SET status = 'delivered', claimed_at = ?, updated_at = ?
                   WHERE id = ?""",
                (now, now, claim_id),
            )
            remaining = conn.execute("SELECT COUNT(*) FROM account_pool WHERE status = 'available'").fetchone()[0]
            conn.commit()
            audit_log.log(
                action="BONUS_CLAIM_DELIVERED",
                actor_id=telegram_id,
                actor_name=username,
                details=f"Claim ID: {claim_id}, Account ID: {account_id}, Remaining available: {remaining}",
                result="SUCCESS",
            )
            return {"status": "delivered", "remaining": int(remaining), "claimed_at": now, "account": account}
        except Exception as exc:
            logger.error("Bonus complete_claim failed for %s: %s", telegram_id, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def fail_claim(claim_id: int, telegram_id: int, account_id: int, reason: str = "DM_FAILED") -> dict:
        conn = db.connect()
        now = BonusService._now().isoformat()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE account_pool
                   SET status = 'available', reserved_at = NULL, assigned_user = NULL, assigned_claim_code = NULL
                   WHERE id = ? AND status = 'reserved'""",
                (account_id,),
            )
            conn.execute(
                """UPDATE bonus_claims
                   SET status = 'failed', failure_reason = ?, failed_at = ?, updated_at = ?
                   WHERE id = ? AND telegram_id = ? AND status = 'in_progress'""",
                (reason, now, now, claim_id, telegram_id),
            )
            conn.commit()
            audit_log.log(
                action="BONUS_CLAIM_FAILED",
                actor_id=telegram_id,
                details=f"Claim ID: {claim_id}, Account ID: {account_id}, Reason: {reason}",
                result="FAILED",
            )
            return {"status": "failed"}
        except Exception as exc:
            logger.error("Bonus fail_claim failed for %s: %s", telegram_id, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "message": str(exc)}


bonus_service = BonusService()
