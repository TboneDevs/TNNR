import logging
import secrets
import uuid
from datetime import datetime
from database.database import db
from services.direct_delivery_service import direct_delivery_service, parse_account_amount
from utils.audit_logger import audit_log

logger = logging.getLogger('tnnr.services.lottery')

class LotteryService:
    """Handles lottery machine giveaway creation, entry processing, and winner selection."""

    @staticmethod
    def create_giveaway(prize: str, win_odds: float, admin_id: int, admin_name: str,
                        announcement_channel_id: int = None, announcement_message_id: int = None,
                        giveaway_id: str = None, status: str = 'active',
                        discussion_group_id: int = None) -> str:
        """
        Create a lottery giveaway.
        win_odds: probability between 0 and 1 (e.g., 0.5 for 50%)
        Returns: giveaway_id
        """
        try:
            # Validate odds
            if not (0 < win_odds <= 1):
                logger.error(f"Invalid odds: {win_odds}")
                return None

            giveaway_id = giveaway_id or f"SPIN-{uuid.uuid4().hex[:6].upper()}"

            cursor = db.execute(
                """INSERT INTO giveaways
                   (giveaway_id, type, prize, status, active_status, winning_number, created_by,
                    created_by_admin_id, created_at, announcement_channel_id,
                    announcement_message_id, discussion_group_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, 'lottery', prize, status, status, int(win_odds * 100),
                 admin_id, admin_id, datetime.now(), announcement_channel_id,
                 announcement_message_id, discussion_group_id)
            )
            db.commit()

            audit_log.log(
                action='LOTTERY_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Admin ID: {admin_id}, Admin Username: {admin_name}, Giveaway Type: spin, Prize: {prize}, Announcement Channel: {announcement_channel_id}, Announcement Message ID: {announcement_message_id}, Discussion Group: {discussion_group_id}, Odds: {win_odds*100}%, Giveaway: {giveaway_id}",
                result='SUCCESS'
            )

            logger.info(f"Created lottery giveaway: {giveaway_id}")
            return giveaway_id
        except Exception as e:
            logger.error(f"Failed to create lottery giveaway: {e}")
            audit_log.log(
                action='LOTTERY_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                result=f'FAILED: {str(e)}'
            )
            return None

    @staticmethod
    def spin_lottery(giveaway_id: str, telegram_id: int, username: str, display_name: str,
                    message_id: int, first_name: str = None, last_name: str = None,
                    source_type: str = None) -> dict:
        """
        Spin lottery machine for a user.
        Returns: {'win': bool, 'prize': str, 'claim_code': str or None}
        """
        try:
            # Get giveaway
            giveaway = db.execute_one(
                "SELECT prize, winning_number FROM giveaways WHERE giveaway_id = ?",
                (giveaway_id,)
            )

            if not giveaway:
                logger.warning(f"Giveaway not found: {giveaway_id}")
                return {'win': False, 'prize': None, 'claim_code': None}

            prize = giveaway[0]
            win_odds = giveaway[1] / 100.0  # Convert back from stored percentage

            # Check if already entered (only one spin per user)
            existing = db.execute_one(
                "SELECT id FROM entries WHERE giveaway_id = ? AND telegram_id = ?",
                (giveaway_id, telegram_id)
            )

            if existing:
                logger.info(f"User {telegram_id} already spun {giveaway_id}")
                return {'win': False, 'prize': None, 'claim_code': None}

            # Spin the machine
            win = secrets.randbelow(100) < (win_odds * 100)

            account_amount = None
            allocation = {'success': False, 'message': None, 'amount': 0}
            if win:
                account_amount = parse_account_amount(prize)
                internal_ref = f"DIRECT-{giveaway_id}-{telegram_id}-{message_id}"

                winner_cursor = db.execute(
                    """INSERT INTO winners
                       (claim_code, giveaway_id, telegram_id, username, display_name, prize, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (internal_ref, giveaway_id, telegram_id, username, display_name, prize, datetime.now())
                )
                db.commit()
                if account_amount:
                    allocation = direct_delivery_service.allocate_owed_accounts(
                        telegram_id=telegram_id,
                        amount=account_amount,
                        source_type='spin_winner',
                        giveaway_id=giveaway_id,
                        prize=prize,
                    )

            # Record entry
            cursor = db.execute(
                """INSERT INTO entries
                   (giveaway_id, telegram_id, username, display_name, message_id, entry_number, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, telegram_id, username, display_name, message_id,
                 1 if win else 0, datetime.now())
            )
            db.commit()

            logger.info(f"Lottery spin: {telegram_id} in {giveaway_id} - {'WIN' if win else 'LOSE'}")

            return {
                'win': win,
                'prize': prize if win else None,
                'owed_amount': account_amount or 0,
                'allocation_success': allocation.get('success', False),
                'allocation_message': allocation.get('message'),
                'winner_telegram_id': telegram_id,
                'winner_username': username,
                'display_name': display_name,
                'source_message_id': message_id,
                'first_name': first_name,
                'last_name': last_name,
                'source_type': source_type,
                'giveaway_id': giveaway_id,
                'giveaway_type': 'spin',
            }
        except Exception as e:
            logger.error(f"Failed to spin lottery: {e}")
            return {'win': False, 'prize': None, 'claim_code': None}

    @staticmethod
    def get_spin_count(giveaway_id: str) -> int:
        """Get number of spins for a giveaway."""
        try:
            result = db.execute_one(
                "SELECT COUNT(*) FROM entries WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Failed to get spin count: {e}")
            return 0

    @staticmethod
    def get_win_count(giveaway_id: str) -> int:
        """Get number of winners for a giveaway."""
        try:
            result = db.execute_one(
                "SELECT COUNT(*) FROM winners WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Failed to get win count: {e}")
            return 0

    @staticmethod
    def get_win_rate(giveaway_id: str) -> float:
        """Get win rate for a giveaway."""
        try:
            spins = LotteryService.get_spin_count(giveaway_id)
            if spins == 0:
                return 0.0

            wins = LotteryService.get_win_count(giveaway_id)
            return (wins / spins) * 100
        except Exception as e:
            logger.error(f"Failed to get win rate: {e}")
            return 0.0

lottery_service = LotteryService()
