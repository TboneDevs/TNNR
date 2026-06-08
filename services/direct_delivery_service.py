"""Free-credit balance and explicit account delivery service.

Unclaimed account credits are stored as account_entitlements keyed by Telegram
user ID.  One credit equals one claimable account.  Credits are free only and
come from giveaways, /bonus, or admin /give allocations.  Accounts are delivered
only through explicit /claim or /withdraw flows. Event credits are promotional: they can be wagered in games, but only winnings become withdrawable account credits.
"""

import logging
import secrets
import re
from datetime import datetime

from database.database import db
from utils.audit_logger import audit_log

logger = logging.getLogger("tnnr.services.direct_delivery")

OUT_OF_CREDITS_MESSAGE = "You are out of credits. Win or receive more unclaimed accounts before playing again."
CLAIM_DM_FAILURE_MESSAGE = "Please open the bot in private messages first, then rerun /claim."
NO_UNCLAIMED_MESSAGE = "You have no unclaimed accounts available."
PROMOTIONAL_WITHDRAW_MESSAGE = "Event promotional credits cannot be withdrawn directly. Use them in /slots or /coinflip first; only winnings become withdrawable credits."
SLOTS_NOTE = "Slots uses 1 credit per spin. Run /slots again to spin another credit."
COINFLIP_WIN_PROBABILITY = 0.50
SLOT_SCALE = 100_000
# The requested winning tier percentages add up to 41% while the requested
# overall game asks for 60% lose / 40% wins / 100% total.  Keep the 60% loss
# rate and scale the requested winning tier weights proportionally to the 40%
# win bucket so the production probability table is valid and exact.
SLOT_TIER_TABLE = (
    ("lose", 60000, 0, "❌"),
    ("bronze_win", 8781, 1, "🥉"),
    ("silver_win", 8781, 2, "🥈"),
    ("gold_win", 7805, 3, "🥇"),
    ("diamond_win", 4878, 4, "💎"),
    ("fire_win", 3902, 5, "🔥"),
    ("rocket_win", 1951, 6, "🚀"),
    ("money_win", 1463, 8, "💰"),
    ("trophy_win", 781, 10, "🏆"),
    ("crown_win", 585, 15, "👑"),
    ("sparkle_win", 390, 20, "💫"),
    ("star_win", 293, 30, "🌟"),
    ("diamond_jackpot", 195, 50, "💎"),
    ("dragon_jackpot", 156, 80, "🐉"),
    ("max_jackpot", 39, 120, "👑"),
)
SLOT_TIERS = tuple((tier, basis_points / SLOT_SCALE, payout) for tier, basis_points, payout, _ in SLOT_TIER_TABLE)
SLOT_TIER_EMOJIS = {tier: emoji for tier, _, _, emoji in SLOT_TIER_TABLE}


def parse_account_amount(prize_text: str) -> int | None:
    """Parse a safe account quantity from free-form prize text."""
    text = prize_text or ""
    match = re.search(r"\b(\d+)\b", text)
    if match:
        amount = int(match.group(1))
        return amount if amount > 0 else None
    if re.search(r"\baccount\b", text, re.IGNORECASE):
        return 1
    return None


def _now() -> str:
    return datetime.utcnow().isoformat()


def calculate_slot_outcome(roll: float) -> tuple[int, str]:
    """Return (credits_won, tier) for a normalized roll in [0, 1).

    The production table is stored in basis points so the requested fractional
    odds (including 0.16% and 0.04%) sum to exactly 100.00% without float drift.
    """
    if roll < 0:
        bucket = 0
    elif roll >= 1:
        bucket = SLOT_SCALE - 1
    else:
        bucket = int(roll * SLOT_SCALE)
    cumulative = 0
    for tier, basis_points, payout, _emoji in SLOT_TIER_TABLE:
        cumulative += basis_points
        if bucket < cumulative:
            return payout, tier
    return SLOT_TIER_TABLE[-1][2], SLOT_TIER_TABLE[-1][0]


class DirectDeliveryService:
    """Persistent unclaimed-credit and account-delivery operations."""

    @staticmethod
    def _ensure_profile(conn, telegram_id: int, username: str | None = None, display_name: str | None = None):
        now = _now()
        conn.execute(
            """INSERT INTO credit_profiles (telegram_id, username, display_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                   username = COALESCE(excluded.username, credit_profiles.username),
                   display_name = COALESCE(excluded.display_name, credit_profiles.display_name),
                   updated_at = excluded.updated_at""",
            (telegram_id, username, display_name, now, now),
        )

    @staticmethod
    def allocate_owed_accounts(telegram_id: int, amount: int, source_type: str = "admin_give",
                               giveaway_id: str | None = None, prize: str | None = None,
                               created_by_admin_id: int | None = None,
                               actor_name: str | None = None, username: str | None = None,
                               display_name: str | None = None, credit_type: str = "withdrawable") -> dict:
        """Add free unclaimed account credits to a user's balance.

        ``credit_type`` is either ``withdrawable`` (claimable accounts) or
        ``promotional`` (event credits that can only be wagered).
        """
        if not isinstance(telegram_id, int) or telegram_id <= 0:
            return {"success": False, "message": "Invalid Telegram ID"}
        if not isinstance(amount, int) or amount <= 0:
            return {"success": False, "message": "Amount must be a positive integer"}
        if credit_type not in {"withdrawable", "promotional"}:
            return {"success": False, "message": "Invalid credit type"}
        conn = db.connect()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            now = _now()
            DirectDeliveryService._ensure_profile(conn, telegram_id, username, display_name)
            conn.execute(
                """INSERT INTO account_entitlements
                   (telegram_id, owed_amount, delivered_amount, status, source_type, giveaway_id,
                    prize, created_by_admin_id, credit_type, created_at, updated_at)
                   VALUES (?, ?, 0, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
                (telegram_id, amount, source_type, giveaway_id, prize, created_by_admin_id, credit_type, now, now),
            )
            conn.execute(
                """UPDATE credit_profiles
                   SET total_won = total_won + ?, updated_at = ?
                   WHERE telegram_id = ?""",
                (amount if credit_type == "withdrawable" else 0, now, telegram_id),
            )
            conn.commit()
            pending = DirectDeliveryService.get_pending_amount(telegram_id)
            audit_log.log(
                action="FREE_CREDITS_ADDED",
                actor_id=created_by_admin_id,
                actor_name=actor_name,
                details=f"Telegram ID: {telegram_id}, Amount: {amount}, Credit type: {credit_type}, Source: {source_type}, Giveaway: {giveaway_id}, Prize: {prize}, New withdrawable balance: {pending}",
                result="SUCCESS",
            )
            return {"success": True, "telegram_id": telegram_id, "amount": amount, "pending": pending}
        except Exception as exc:
            logger.error("Failed to allocate free credits: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    @staticmethod
    def admin_give(admin_id: int, admin_name: str, telegram_id: int, amount: int) -> dict:
        result = DirectDeliveryService.allocate_owed_accounts(
            telegram_id=telegram_id,
            amount=amount,
            source_type="admin_give",
            created_by_admin_id=admin_id,
            actor_name=admin_name,
            prize=f"{amount} Account{'s' if amount != 1 else ''}",
        )
        if result.get("success"):
            audit_log.log(
                action="ADMIN_GIVE_CREDITS",
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Assigned {amount} free account credit(s) to Telegram ID {telegram_id}; new balance {result.get('pending')}",
                result="SUCCESS",
            )
        return result

    @staticmethod
    def get_pending_entitlements(telegram_id: int, credit_type: str = "withdrawable") -> list[dict]:
        rows = db.execute_all(
            """SELECT id, telegram_id, owed_amount, delivered_amount, status, source_type, giveaway_id, prize, credit_type, created_at
               FROM account_entitlements
               WHERE telegram_id = ? AND credit_type = ? AND status IN ('pending', 'partial')
                 AND owed_amount > delivered_amount
               ORDER BY created_at ASC, id ASC""",
            (telegram_id, credit_type),
        )
        return [dict(row) for row in rows]

    @staticmethod
    def _get_amount_by_type(telegram_id: int, credit_type: str) -> int:
        row = db.execute_one(
            """SELECT COALESCE(SUM(owed_amount - delivered_amount), 0)
               FROM account_entitlements
               WHERE telegram_id = ? AND credit_type = ? AND status IN ('pending', 'partial')
                 AND owed_amount > delivered_amount""",
            (telegram_id, credit_type),
        )
        return max(0, int(row[0] or 0)) if row else 0

    @staticmethod
    def get_pending_amount(telegram_id: int) -> int:
        """Withdrawable account credits available for /claim or /withdraw."""
        return DirectDeliveryService._get_amount_by_type(telegram_id, "withdrawable")

    @staticmethod
    def get_promotional_amount(telegram_id: int) -> int:
        """Promotional event credits available for games only."""
        return DirectDeliveryService._get_amount_by_type(telegram_id, "promotional")

    @staticmethod
    def get_playable_amount(telegram_id: int) -> int:
        return DirectDeliveryService.get_pending_amount(telegram_id) + DirectDeliveryService.get_promotional_amount(telegram_id)

    @staticmethod
    def get_balance_summary(telegram_id: int, username: str | None = None, display_name: str | None = None) -> dict:
        conn = db.connect()
        DirectDeliveryService._ensure_profile(conn, telegram_id, username, display_name)
        conn.commit()
        profile = db.execute_one(
            "SELECT total_won, total_claimed, current_bet FROM credit_profiles WHERE telegram_id = ?",
            (telegram_id,),
        )
        withdrawable = DirectDeliveryService.get_pending_amount(telegram_id)
        promotional = DirectDeliveryService.get_promotional_amount(telegram_id)
        return {
            "balance": withdrawable,
            "withdrawable_balance": withdrawable,
            "promotional_balance": promotional,
            "playable_balance": withdrawable + promotional,
            "total_won": int(profile[0] or 0) if profile else 0,
            "total_claimed": int(profile[1] or 0) if profile else 0,
            "current_bet": int(profile[2] or 1) if profile else 1,
        }

    @staticmethod
    def set_current_bet(telegram_id: int, amount: int, username: str | None = None, display_name: str | None = None) -> dict:
        if not isinstance(amount, int) or amount <= 0:
            return {"success": False, "message": "Usage: /bet 1"}
        balance = DirectDeliveryService.get_playable_amount(telegram_id)
        if balance < amount:
            return {"success": False, "message": OUT_OF_CREDITS_MESSAGE}
        conn = db.connect()
        try:
            DirectDeliveryService._ensure_profile(conn, telegram_id, username, display_name)
            conn.execute(
                "UPDATE credit_profiles SET current_bet = ?, updated_at = ? WHERE telegram_id = ?",
                (amount, _now(), telegram_id),
            )
            conn.commit()
            return {"success": True, "bet": amount, "balance": balance}
        except Exception as exc:
            logger.error("Failed to set bet: %s", exc)
            db.rollback()
            return {"success": False, "message": str(exc)}

    @staticmethod
    def _subtract_credits(conn, telegram_id: int, amount: int, credit_type: str = "withdrawable"):
        remaining = amount
        rows = conn.execute(
            """SELECT id, owed_amount, delivered_amount
               FROM account_entitlements
               WHERE telegram_id = ? AND credit_type = ? AND status IN ('pending', 'partial')
                 AND owed_amount > delivered_amount
               ORDER BY created_at ASC, id ASC""",
            (telegram_id, credit_type),
        ).fetchall()
        for row in rows:
            if remaining <= 0:
                break
            entitlement_id, owed_amount, delivered_amount = row[0], int(row[1]), int(row[2] or 0)
            available = owed_amount - delivered_amount
            take = min(available, remaining)
            new_delivered = delivered_amount + take
            new_status = "fulfilled" if new_delivered >= owed_amount else "partial"
            conn.execute(
                "UPDATE account_entitlements SET delivered_amount = ?, status = ?, updated_at = ? WHERE id = ?",
                (new_delivered, new_status, _now(), entitlement_id),
            )
            remaining -= take
        if remaining != 0:
            raise RuntimeError("Insufficient credits during debit")

    @staticmethod
    def _add_game_credits(conn, telegram_id: int, amount: int, source_type: str, username: str | None = None, credit_type: str = "withdrawable") :
        if amount <= 0:
            return
        now = _now()
        conn.execute(
            """INSERT INTO account_entitlements
               (telegram_id, owed_amount, delivered_amount, status, source_type, prize, credit_type, created_at, updated_at)
               VALUES (?, ?, 0, 'pending', ?, ?, ?, ?, ?)""",
            (telegram_id, amount, source_type, f"{amount} Free Credit{'s' if amount != 1 else ''}", credit_type, now, now),
        )
        conn.execute(
            "UPDATE credit_profiles SET total_won = total_won + ?, updated_at = ? WHERE telegram_id = ?",
            (amount if credit_type == "withdrawable" else 0, now, telegram_id),
        )

    @staticmethod
    def _log_game(conn, telegram_id: int, username: str | None, command: str, amount_wagered: int,
                  amount_won: int, amount_lost: int, new_balance: int, result: str, details: str = ""):
        conn.execute(
            """INSERT INTO credit_game_logs
               (telegram_id, username, command, amount_wagered, amount_won, amount_lost, new_balance, result, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (telegram_id, username, command, amount_wagered, amount_won, amount_lost, new_balance, result, details, _now()),
        )

    @staticmethod
    def _choose_wager_source(telegram_id: int, amount: int = 1) -> tuple[str | None, int, int]:
        """Prefer promotional credits for game wagers, then withdrawable credits."""
        promotional = DirectDeliveryService.get_promotional_amount(telegram_id)
        withdrawable = DirectDeliveryService.get_pending_amount(telegram_id)
        if promotional >= amount:
            return "promotional", promotional, withdrawable
        if withdrawable >= amount:
            return "withdrawable", promotional, withdrawable
        return None, promotional, withdrawable

    @staticmethod
    def play_slots(telegram_id: int, username: str | None = None, roll: float | None = None) -> dict:
        conn = db.connect()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            DirectDeliveryService._ensure_profile(conn, telegram_id, username)
            source, promotional_balance, withdrawable_balance = DirectDeliveryService._choose_wager_source(telegram_id, 1)
            playable_balance = promotional_balance + withdrawable_balance
            if source is None:
                conn.commit()
                return {"success": False, "message": OUT_OF_CREDITS_MESSAGE, "balance": withdrawable_balance, "promotional_balance": promotional_balance, "playable_balance": playable_balance}
            DirectDeliveryService._subtract_credits(conn, telegram_id, 1, source)
            r = (secrets.randbelow(SLOT_SCALE) / SLOT_SCALE) if roll is None else roll
            # Probability buckets total exactly 100.00% with 60% loss
            # and the requested winning tier weights scaled into a 40% win bucket.
            won, tier = calculate_slot_outcome(r)
            if won:
                DirectDeliveryService._add_game_credits(conn, telegram_id, won, "slots_win", username, "withdrawable")
            conn.execute(
                "UPDATE credit_profiles SET slots_played = slots_played + 1, updated_at = ? WHERE telegram_id = ?",
                (_now(), telegram_id),
            )
            new_balance = DirectDeliveryService.get_pending_amount(telegram_id)
            new_promotional = DirectDeliveryService.get_promotional_amount(telegram_id)
            new_playable = new_balance + new_promotional
            DirectDeliveryService._log_game(conn, telegram_id, username, "/slots", 1, won, 0 if won else 1, new_playable, tier, f"source={source}; withdrawable_balance={new_balance}; promotional_balance={new_promotional}")
            conn.commit()
            audit_log.log(
                action="SLOTS_PLAYED",
                actor_id=telegram_id,
                actor_name=username,
                details=f"Wagered: 1, Source: {source}, Won: {won}, Result: {tier}, New playable balance: {new_playable}, Withdrawable balance: {new_balance}, Promotional balance: {new_promotional}",
                result="SUCCESS",
            )
            return {"success": True, "won": won, "tier": tier, "emoji": SLOT_TIER_EMOJIS.get(tier, "💎"), "balance": new_playable, "playable_balance": new_playable, "withdrawable_balance": new_balance, "promotional_balance": new_promotional, "wager_source": source}
        except Exception as exc:
            logger.error("Slots play failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    @staticmethod
    def play_coinflip(telegram_id: int, choice: str, username: str | None = None, roll: float | None = None) -> dict:
        normalized = (choice or "").strip().lower()
        if normalized not in {"heads", "tails"}:
            return {"success": False, "message": "Usage: /coinflip heads OR /coinflip tails"}
        conn = db.connect()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            DirectDeliveryService._ensure_profile(conn, telegram_id, username)
            source, promotional_balance, withdrawable_balance = DirectDeliveryService._choose_wager_source(telegram_id, 1)
            playable_balance = promotional_balance + withdrawable_balance
            if source is None:
                conn.commit()
                return {"success": False, "message": OUT_OF_CREDITS_MESSAGE, "balance": withdrawable_balance, "promotional_balance": promotional_balance, "playable_balance": playable_balance}
            DirectDeliveryService._subtract_credits(conn, telegram_id, 1, source)
            r = (secrets.randbelow(2) / 2) if roll is None else roll
            won = r < COINFLIP_WIN_PROBABILITY
            payout = 2 if won else 0
            credited = 1 if (won and source == "promotional") else payout
            if credited:
                DirectDeliveryService._add_game_credits(conn, telegram_id, credited, "coinflip_win", username, "withdrawable")
            conn.execute(
                "UPDATE credit_profiles SET coinflips_played = coinflips_played + 1, updated_at = ? WHERE telegram_id = ?",
                (_now(), telegram_id),
            )
            new_balance = DirectDeliveryService.get_pending_amount(telegram_id)
            new_promotional = DirectDeliveryService.get_promotional_amount(telegram_id)
            DirectDeliveryService._log_game(conn, telegram_id, username, "/coinflip", 1, credited, 0 if won else 1, new_balance, "win" if won else "loss", f"choice={normalized}; source={source}; payout={payout}; promotional_balance={new_promotional}")
            conn.commit()
            audit_log.log(
                action="COINFLIP_PLAYED",
                actor_id=telegram_id,
                actor_name=username,
                details=f"Choice: {normalized}, Wagered: 1, Source: {source}, Withdrawable credited: {credited}, New withdrawable balance: {new_balance}, Promotional balance: {new_promotional}",
                result="SUCCESS",
            )
            return {"success": True, "won": won, "payout": credited, "raw_payout": payout, "choice": normalized, "balance": new_balance, "withdrawable_balance": new_balance, "promotional_balance": new_promotional, "wager_source": source}
        except Exception as exc:
            logger.error("Coinflip failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    @staticmethod
    def prepare_claim_for_user(telegram_id: int, username: str | None = None, trigger: str = "claim") -> dict:
        """Reserve up to the available stock for explicit /claim or /withdraw.

        Entitlement balances are not reduced until the caller confirms that the
        DM containing account credentials was sent successfully.
        """
        conn = db.connect()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            owed_total = DirectDeliveryService.get_pending_amount(telegram_id)
            promotional_total = DirectDeliveryService.get_promotional_amount(telegram_id)
            if owed_total <= 0:
                conn.commit()
                if promotional_total > 0:
                    return {"status": "promotional_only", "success": False, "owed_amount": 0, "promotional_amount": promotional_total, "accounts": []}
                return {"status": "no_pending", "success": False, "owed_amount": 0, "accounts": []}
            available_count = conn.execute("SELECT COUNT(*) FROM account_pool WHERE status = 'available'").fetchone()[0]
            if available_count <= 0:
                now = _now()
                conn.execute(
                    """INSERT INTO account_delivery_logs
                       (telegram_id, amount_requested, amount_delivered, status, reason, trigger, created_at)
                       VALUES (?, ?, 0, 'failed', 'INSUFFICIENT_STOCK', ?, ?)""",
                    (telegram_id, owed_total, trigger, now),
                )
                conn.commit()
                audit_log.log(
                    action="CLAIM_FAILED_LOW_STOCK",
                    actor_id=telegram_id,
                    actor_name=username,
                    details=f"Requested: {owed_total}, Available: 0, Trigger: {trigger}",
                    result="FAILED",
                )
                return {"status": "insufficient_stock", "success": False, "owed_amount": owed_total, "available": 0, "accounts": []}
            deliver_count = min(owed_total, int(available_count))
            accounts = conn.execute(
                "SELECT id, email, password FROM account_pool WHERE status = 'available' ORDER BY id ASC LIMIT ?",
                (deliver_count,),
            ).fetchall()
            now = _now()
            ref = f"CLAIM-{telegram_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
            account_ids = [int(row[0]) for row in accounts]
            for account_id in account_ids:
                conn.execute(
                    """UPDATE account_pool
                       SET status = 'reserved', reserved_at = ?, assigned_user = ?, assigned_claim_code = ?
                       WHERE id = ? AND status = 'available'""",
                    (now, telegram_id, ref, account_id),
                )
            cursor = conn.execute(
                """INSERT INTO account_delivery_logs
                   (telegram_id, amount_requested, amount_delivered, status, reason, trigger, account_ids, created_at)
                   VALUES (?, ?, ?, 'in_progress', ?, ?, ?, ?)""",
                (telegram_id, owed_total, deliver_count, 'PARTIAL_STOCK' if deliver_count < owed_total else None, trigger, ",".join(map(str, account_ids)), now),
            )
            conn.commit()
            return {
                "status": "reserved", "success": True, "delivery_log_id": cursor.lastrowid,
                "delivery_ref": ref, "accounts": [f"{row[1]}:{row[2]}" for row in accounts],
                "account_ids": account_ids, "owed_amount": owed_total, "reserved_amount": deliver_count,
                "partial": deliver_count < owed_total, "available": int(available_count),
            }
        except Exception as exc:
            logger.error("Prepare claim failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "success": False, "message": str(exc), "accounts": []}

    @staticmethod
    def complete_prepared_claim(telegram_id: int, delivery_log_id: int, account_ids: list[int], amount: int,
                                username: str | None = None, trigger: str = "claim") -> dict:
        conn = db.connect()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            now = _now()
            for account_id in account_ids:
                conn.execute(
                    """UPDATE account_pool
                       SET status = 'delivered', delivered_at = ?, assigned_user = ?
                       WHERE id = ? AND status = 'reserved' AND assigned_user = ?""",
                    (now, telegram_id, account_id, telegram_id),
                )
            DirectDeliveryService._subtract_credits(conn, telegram_id, amount)
            conn.execute(
                "UPDATE credit_profiles SET total_claimed = total_claimed + ?, updated_at = ? WHERE telegram_id = ?",
                (amount, now, telegram_id),
            )
            remaining_balance = DirectDeliveryService.get_pending_amount(telegram_id)
            remaining_pool = conn.execute("SELECT COUNT(*) FROM account_pool WHERE status = 'available'").fetchone()[0]
            conn.execute(
                """UPDATE account_delivery_logs
                   SET status = 'delivered', amount_delivered = ?, reason = NULL, created_at = created_at
                   WHERE id = ? AND telegram_id = ?""",
                (amount, delivery_log_id, telegram_id),
            )
            conn.commit()
            audit_log.log(
                action="ACCOUNTS_CLAIMED",
                actor_id=telegram_id,
                actor_name=username,
                details=f"Command: /{trigger}, Accounts sent: {amount}, New balance: {remaining_balance}, Remaining pool: {remaining_pool}",
                result="SUCCESS",
            )
            return {"status": "delivered", "success": True, "accounts_delivered": amount, "balance": remaining_balance, "remaining_pool": int(remaining_pool)}
        except Exception as exc:
            logger.error("Complete claim failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "success": False, "message": str(exc)}

    @staticmethod
    def fail_prepared_claim(telegram_id: int, delivery_log_id: int, account_ids: list[int], reason: str = "DM_FAILED",
                            username: str | None = None, trigger: str = "claim") -> dict:
        conn = db.connect()
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            now = _now()
            for account_id in account_ids:
                conn.execute(
                    """UPDATE account_pool
                       SET status = 'available', reserved_at = NULL, assigned_user = NULL, assigned_claim_code = NULL
                       WHERE id = ? AND status = 'reserved' AND assigned_user = ?""",
                    (account_id, telegram_id),
                )
            conn.execute(
                """UPDATE account_delivery_logs
                   SET status = 'failed', reason = ?
                   WHERE id = ? AND telegram_id = ?""",
                (reason, delivery_log_id, telegram_id),
            )
            conn.commit()
            audit_log.log(
                action="CLAIM_DELIVERY_FAILED",
                actor_id=telegram_id,
                actor_name=username,
                details=f"Command: /{trigger}, Reason: {reason}, Account IDs returned: {account_ids}, Time: {now}",
                result="FAILED",
            )
            return {"status": "failed"}
        except Exception as exc:
            logger.error("Fail prepared claim failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def attempt_delivery_for_user(telegram_id: int, username: str | None = None, trigger: str = "dm") -> dict:
        """Legacy service API retained for tests/backward compatibility.

        New handlers do not call this automatically from /start or normal DMs.
        """
        prepared = DirectDeliveryService.prepare_claim_for_user(telegram_id, username, trigger)
        if prepared.get("status") != "reserved":
            return prepared
        completed = DirectDeliveryService.complete_prepared_claim(
            telegram_id, prepared["delivery_log_id"], prepared["account_ids"], prepared["reserved_amount"], username, trigger
        )
        completed.update({"accounts": prepared.get("accounts", []), "owed_amount": prepared.get("owed_amount"), "delivery_ref": prepared.get("delivery_ref")})
        return completed

    @staticmethod
    def get_leaderboard(limit: int = 10) -> dict:
        balance_rows = db.execute_all(
            """SELECT p.telegram_id, p.username, p.total_won, p.total_claimed,
                      COALESCE(SUM(CASE WHEN e.status IN ('pending','partial') AND e.credit_type = 'withdrawable' THEN e.owed_amount - e.delivered_amount ELSE 0 END), 0) AS balance,
                      COALESCE(SUM(CASE WHEN e.status IN ('pending','partial') AND e.credit_type = 'promotional' THEN e.owed_amount - e.delivered_amount ELSE 0 END), 0) AS promotional_balance
               FROM credit_profiles p
               LEFT JOIN account_entitlements e ON e.telegram_id = p.telegram_id
               GROUP BY p.telegram_id
               ORDER BY balance DESC, p.total_won DESC
               LIMIT ?""",
            (limit,),
        )
        won_rows = db.execute_all(
            "SELECT telegram_id, username, total_won, total_claimed FROM credit_profiles ORDER BY total_won DESC LIMIT ?",
            (limit,),
        )
        claimed_rows = db.execute_all(
            "SELECT telegram_id, username, total_won, total_claimed FROM credit_profiles ORDER BY total_claimed DESC LIMIT ?",
            (limit,),
        )
        return {
            "balance": [dict(r) for r in balance_rows],
            "total_won": [dict(r) for r in won_rows],
            "total_claimed": [dict(r) for r in claimed_rows],
        }


direct_delivery_service = DirectDeliveryService()
