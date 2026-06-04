import logging
import secrets
import uuid
from datetime import datetime
from database.database import db
from utils.claimcode import generate_claim_code
from utils.audit_logger import audit_log

logger = logging.getLogger('tnnr.services.lottery')

class LotteryService:
    """Handles lottery machine giveaway creation, entry processing, and winner selection."""
    
    @staticmethod
    def create_giveaway(prize: str, win_odds: float, admin_id: int, admin_name: str) -> str:
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
            
            giveaway_id = f"LOTTERY-{uuid.uuid4().hex[:6].upper()}"
            
            cursor = db.execute(
                """INSERT INTO giveaways 
                   (giveaway_id, type, prize, status, winning_number, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (giveaway_id, 'lottery', prize, 'draft', int(win_odds * 100), 
                 admin_id, datetime.now())
            )
            db.commit()
            
            audit_log.log(
                action='LOTTERY_GIVEAWAY_CREATED',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Prize: {prize}, Odds: {win_odds*100}%, Giveaway: {giveaway_id}",
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
                    message_id: int) -> dict:
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
            
            claim_code = None
            if win:
                # Generate claim code
                claim_code = generate_claim_code()
                
                # Create winner record
                cursor = db.execute(
                    """INSERT INTO winners 
                       (claim_code, giveaway_id, telegram_id, username, display_name, prize, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (claim_code, giveaway_id, telegram_id, username, display_name, prize, datetime.now())
                )
                db.commit()
            
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
                'claim_code': claim_code
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
