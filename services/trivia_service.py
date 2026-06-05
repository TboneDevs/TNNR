import logging
import secrets
import uuid
from datetime import datetime
from database.database import db
from services.direct_delivery_service import direct_delivery_service, parse_account_amount
from utils.validators import normalize_text
from utils.audit_logger import audit_log

logger = logging.getLogger('tnnr.services.trivia')

class TriviaService:
    """Handles trivia giveaway creation, entry processing, and winner selection."""

    @staticmethod
    def create_giveaway(question: str, answer: str, prize: str, admin_id: int, admin_name: str,
                        announcement_channel_id: int = None, announcement_message_id: int = None,
                        giveaway_id: str = None, status: str = 'active',
                        discussion_group_id: int = None) -> str:
        """
        Create a trivia giveaway.
        Returns: giveaway_id
        """
        try:
            giveaway_id = giveaway_id or f"TRIVIA-{uuid.uuid4().hex[:6].upper()}"
            normalized_answer = normalize_text(answer)

            db.execute(
                """INSERT INTO giveaways
                   (giveaway_id, type, prize, status, active_status, hidden_answer, created_by,
                    created_by_admin_id, created_at, announcement_channel_id,
                    announcement_message_id, discussion_group_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, 'trivia', prize, status, status, normalized_answer, admin_id,
                 admin_id, datetime.now(), announcement_channel_id, announcement_message_id,
                 discussion_group_id)
            )
            db.commit()

            audit_log.log(
                action='TRIVIA_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Admin ID: {admin_id}, Admin Username: {admin_name}, Giveaway Type: trivia, Prize: {prize}, Announcement Channel: {announcement_channel_id}, Announcement Message ID: {announcement_message_id}, Discussion Group: {discussion_group_id}, Giveaway: {giveaway_id}",
                result='SUCCESS'
            )

            logger.info(f"Created trivia giveaway: {giveaway_id}")
            return giveaway_id
        except Exception as e:
            logger.error(f"Failed to create trivia giveaway: {e}")
            audit_log.log(
                action='TRIVIA_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                result=f'FAILED: {str(e)}'
            )
            return None

    @staticmethod
    def submit_entry(giveaway_id: str, telegram_id: int, username: str, display_name: str,
                     message_id: int, entry_text: str, first_name: str = None,
                     last_name: str = None, source_type: str = 'discussion_group') -> bool:
        """
        Process a trivia entry.
        Returns: True if entry accepted, False otherwise
        """
        try:
            giveaway = db.execute_one(
                "SELECT hidden_answer FROM giveaways WHERE giveaway_id = ? AND type = 'trivia' AND status = 'active'",
                (giveaway_id,)
            )

            if not giveaway:
                logger.warning(f"Giveaway not found: {giveaway_id}")
                return False

            existing = db.execute_one(
                "SELECT id FROM entries WHERE giveaway_id = ? AND telegram_id = ?",
                (giveaway_id, telegram_id)
            )

            if existing:
                logger.info(f"User {telegram_id} already entered {giveaway_id}")
                return False

            normalized_entry = normalize_text(entry_text)
            hidden_answer = giveaway[0]

            if normalized_entry != hidden_answer:
                logger.info(f"Incorrect answer from {telegram_id} in {giveaway_id}")
                return False

            db.execute(
                """INSERT INTO entries
                   (giveaway_id, telegram_id, username, first_name, last_name, display_name,
                    message_id, entry_text, submitted_answer, source_type, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, telegram_id, username, first_name, last_name, display_name,
                 message_id, entry_text, entry_text, source_type, datetime.now())
            )
            db.commit()

            logger.info(f"Trivia entry accepted: {telegram_id} in {giveaway_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to process trivia entry: {e}")
            return False

    @staticmethod
    def select_winner(giveaway_id: str, admin_id: int, admin_name: str) -> dict:
        """
        Select a winner from trivia entries.
        Returns winner details or None.
        """
        try:
            entries = db.execute_all(
                """SELECT id, telegram_id, username, display_name, giveaway_id, message_id,
                          first_name, last_name, source_type
                   FROM entries WHERE giveaway_id = ?""",
                (giveaway_id,)
            )

            if not entries:
                logger.error(f"No entries for giveaway: {giveaway_id}")
                return None

            winner_entry = entries[secrets.randbelow(len(entries))]
            giveaway = db.execute_one(
                "SELECT prize FROM giveaways WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            prize = giveaway[0]
            account_amount = parse_account_amount(prize)
            internal_ref = f"DIRECT-{giveaway_id}-{winner_entry[1]}"

            winner_cursor = db.execute(
                """INSERT INTO winners
                   (claim_code, giveaway_id, telegram_id, username, display_name, prize, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (internal_ref, giveaway_id, winner_entry[1], winner_entry[2],
                 winner_entry[3], prize, datetime.now())
            )
            db.commit()
            allocation = {'success': False, 'message': 'Could not determine account quantity', 'amount': 0}
            if account_amount:
                allocation = direct_delivery_service.allocate_owed_accounts(
                    telegram_id=winner_entry[1],
                    amount=account_amount,
                    source_type='trivia_winner',
                    giveaway_id=giveaway_id,
                    prize=prize,
                    created_by_admin_id=admin_id,
                    actor_name=admin_name,
                    username=winner_entry[2],
                    display_name=winner_entry[3],
                )
            db.execute(
                "UPDATE giveaways SET status = 'winner_selected', active_status = 'winner_selected' WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            db.commit()

            audit_log.log(
                action='TRIVIA_WINNER_SELECTED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Giveaway: {giveaway_id}, Winner: {winner_entry[2]} ({winner_entry[1]}), Prize: {prize}, Source Message ID: {winner_entry[5]}",
                result='SUCCESS'
            )

            logger.info(f"Winner selected: {winner_entry[1]} for {giveaway_id}")
            return {
                'winner_telegram_id': winner_entry[1],
                'winner_username': winner_entry[2],
                'display_name': winner_entry[3],
                'source_message_id': winner_entry[5],
                'first_name': winner_entry[6],
                'last_name': winner_entry[7],
                'source_type': winner_entry[8],
                'owed_amount': account_amount or 0,
                'allocation_success': allocation.get('success', False),
                'allocation_message': allocation.get('message'),
                'prize': prize,
                'giveaway_id': giveaway_id,
                'giveaway_type': 'trivia',
            }
        except Exception as e:
            logger.error(f"Failed to select winner: {e}")
            audit_log.log(
                action='TRIVIA_WINNER_SELECTED',
                actor_id=admin_id,
                actor_name=admin_name,
                result=f'FAILED: {str(e)}'
            )
            return None

    @staticmethod
    def get_entry_count(giveaway_id: str) -> int:
        """Get number of entries for a giveaway."""
        try:
            result = db.execute_one(
                "SELECT COUNT(*) FROM entries WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Failed to get entry count: {e}")
            return 0

trivia_service = TriviaService()
