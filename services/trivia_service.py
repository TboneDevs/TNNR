import logging
import secrets
import uuid
from datetime import datetime
from database.database import db
from utils.claimcode import generate_claim_code
from utils.validators import normalize_text
from utils.audit_logger import audit_log

logger = logging.getLogger('tnnr.services.trivia')

class TriviaService:
    """Handles trivia giveaway creation, entry processing, and winner selection."""
    
    @staticmethod
    def create_giveaway(question: str, answer: str, prize: str, admin_id: int, admin_name: str) -> str:
        """
        Create a trivia giveaway.
        Returns: giveaway_id
        """
        try:
            giveaway_id = f"TRIVIA-{uuid.uuid4().hex[:6].upper()}"
            normalized_answer = normalize_text(answer)
            
            cursor = db.execute(
                """INSERT INTO giveaways 
                   (giveaway_id, type, prize, status, hidden_answer, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, 'trivia', prize, 'draft', normalized_answer, admin_id, datetime.now())
            )
            db.commit()
            
            audit_log.log(
                action='TRIVIA_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Prize: {prize}, Giveaway: {giveaway_id}",
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
                     message_id: int, entry_text: str) -> bool:
        """
        Process a trivia entry.
        Returns: True if entry accepted, False otherwise
        """
        try:
            # Get giveaway
            giveaway = db.execute_one(
                "SELECT * FROM giveaways WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            
            if not giveaway:
                logger.warning(f"Giveaway not found: {giveaway_id}")
                return False
            
            # Check if already entered
            existing = db.execute_one(
                "SELECT id FROM entries WHERE giveaway_id = ? AND telegram_id = ?",
                (giveaway_id, telegram_id)
            )
            
            if existing:
                logger.info(f"User {telegram_id} already entered {giveaway_id}")
                return False
            
            # Normalize and compare answer
            normalized_entry = normalize_text(entry_text)
            hidden_answer = giveaway[8]  # hidden_answer column
            
            if normalized_entry != hidden_answer:
                logger.info(f"Incorrect answer from {telegram_id} in {giveaway_id}")
                return False
            
            # Store correct entry
            cursor = db.execute(
                """INSERT INTO entries 
                   (giveaway_id, telegram_id, username, display_name, message_id, entry_text, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, telegram_id, username, display_name, message_id, entry_text, datetime.now())
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
        Returns: {'winner_id', 'claim_code'} or None
        """
        try:
            # Get entries
            entries = db.execute_all(
                """SELECT id, telegram_id, username, display_name, giveaway_id 
                   FROM entries WHERE giveaway_id = ?""",
                (giveaway_id,)
            )
            
            if not entries:
                logger.error(f"No entries for giveaway: {giveaway_id}")
                return None
            
            # Select random winner
            winner_entry = entries[secrets.randbelow(len(entries))]
            
            # Get giveaway details
            giveaway = db.execute_one(
                "SELECT prize FROM giveaways WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            
            prize = giveaway[0]
            
            # Generate claim code
            claim_code = generate_claim_code()
            
            # Create winner record
            cursor = db.execute(
                """INSERT INTO winners 
                   (claim_code, giveaway_id, telegram_id, username, display_name, prize, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (claim_code, giveaway_id, winner_entry[1], winner_entry[2], 
                 winner_entry[3], prize, datetime.now())
            )
            db.commit()
            
            # Update giveaway status
            db.execute(
                "UPDATE giveaways SET status = 'winner_selected' WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            db.commit()
            
            audit_log.log(
                action='TRIVIA_WINNER_SELECTED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Giveaway: {giveaway_id}, Winner: {winner_entry[2]} ({winner_entry[1]}), Prize: {prize}",
                result='SUCCESS'
            )
            
            logger.info(f"Winner selected: {winner_entry[1]} for {giveaway_id}")
            return {
                'winner_telegram_id': winner_entry[1],
                'winner_username': winner_entry[2],
                'claim_code': claim_code,
                'prize': prize
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
