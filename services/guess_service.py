import logging
import secrets
import uuid
from datetime import datetime
from database.database import db
from utils.claimcode import generate_claim_code
from utils.validators import validate_number
from utils.audit_logger import audit_log

logger = logging.getLogger('tnnr.services.guess')

class GuessService:
    """Handles number guess giveaway creation, entry processing, and winner selection."""
    
    @staticmethod
    def create_giveaway(min_num: int, max_num: int, winning_num: int, prize: str, 
                       admin_id: int, admin_name: str) -> str:
        """
        Create a number guess giveaway.
        Returns: giveaway_id
        """
        try:
            # Validate inputs
            if min_num >= max_num or winning_num < min_num or winning_num > max_num:
                logger.error("Invalid number range for guess giveaway")
                return None
            
            giveaway_id = f"GUESS-{uuid.uuid4().hex[:6].upper()}"
            
            cursor = db.execute(
                """INSERT INTO giveaways 
                   (giveaway_id, type, prize, status, min_number, max_number, 
                    winning_number, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, 'guess', prize, 'draft', min_num, max_num, 
                 winning_num, admin_id, datetime.now())
            )
            db.commit()
            
            audit_log.log(
                action='GUESS_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Range: {min_num}-{max_num}, Prize: {prize}, Giveaway: {giveaway_id}",
                result='SUCCESS'
            )
            
            logger.info(f"Created guess giveaway: {giveaway_id}")
            return giveaway_id
        except Exception as e:
            logger.error(f"Failed to create guess giveaway: {e}")
            audit_log.log(
                action='GUESS_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                result=f'FAILED: {str(e)}'
            )
            return None
    
    @staticmethod
    def submit_entry(giveaway_id: str, telegram_id: int, username: str, display_name: str,
                     message_id: int, guess_text: str) -> bool:
        """
        Process a number guess entry.
        Returns: True if entry accepted, False otherwise
        """
        try:
            # Get giveaway
            giveaway = db.execute_one(
                "SELECT min_number, max_number FROM giveaways WHERE giveaway_id = ?",
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
            
            # Validate number
            min_num, max_num = giveaway[0], giveaway[1]
            is_valid, guess_num = validate_number(guess_text, min_num, max_num)
            
            if not is_valid:
                logger.info(f"Invalid guess from {telegram_id} in {giveaway_id}")
                return False
            
            # Store entry
            cursor = db.execute(
                """INSERT INTO entries 
                   (giveaway_id, telegram_id, username, display_name, message_id, 
                    entry_text, entry_number, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, telegram_id, username, display_name, message_id, 
                 guess_text, guess_num, datetime.now())
            )
            db.commit()
            
            logger.info(f"Guess entry accepted: {telegram_id} guessed {guess_num} in {giveaway_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to process guess entry: {e}")
            return False
    
    @staticmethod
    def select_winner(giveaway_id: str, admin_id: int, admin_name: str) -> dict:
        """
        Select a winner from guess entries.
        Exact match wins immediately. Otherwise closest guess wins.
        Returns: {'winner_id', 'claim_code'} or None
        """
        try:
            # Get giveaway details
            giveaway = db.execute_one(
                "SELECT winning_number, prize FROM giveaways WHERE giveaway_id = ?",
                (giveaway_id,)
            )
            
            if not giveaway:
                logger.error(f"Giveaway not found: {giveaway_id}")
                return None
            
            winning_number = giveaway[0]
            prize = giveaway[1]
            
            # Get entries
            entries = db.execute_all(
                """SELECT id, telegram_id, username, display_name, entry_number 
                   FROM entries WHERE giveaway_id = ? ORDER BY entry_number""",
                (giveaway_id,)
            )
            
            if not entries:
                logger.error(f"No entries for giveaway: {giveaway_id}")
                return None
            
            # Find exact match or closest
            exact_matches = [e for e in entries if e[4] == winning_number]
            
            if exact_matches:
                # Multiple exact matches - select random
                winner_entry = exact_matches[secrets.randbelow(len(exact_matches))]
            else:
                # Find closest
                closest_entries = []
                min_diff = float('inf')
                
                for entry in entries:
                    diff = abs(entry[4] - winning_number)
                    if diff < min_diff:
                        min_diff = diff
                        closest_entries = [entry]
                    elif diff == min_diff:
                        closest_entries.append(entry)
                
                # Tie-break with random selection
                winner_entry = closest_entries[secrets.randbelow(len(closest_entries))]
            
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
                action='GUESS_WINNER_SELECTED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Giveaway: {giveaway_id}, Winner: {winner_entry[2]} ({winner_entry[1]}), Guess: {winner_entry[4]}, Winning: {winning_number}, Prize: {prize}",
                result='SUCCESS'
            )
            
            logger.info(f"Winner selected: {winner_entry[1]} (guess: {winner_entry[4]}) for {giveaway_id}")
            return {
                'winner_telegram_id': winner_entry[1],
                'winner_username': winner_entry[2],
                'winning_number': winning_number,
                'guess': winner_entry[4],
                'claim_code': claim_code,
                'prize': prize
            }
        except Exception as e:
            logger.error(f"Failed to select winner: {e}")
            audit_log.log(
                action='GUESS_WINNER_SELECTED',
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

guess_service = GuessService()
