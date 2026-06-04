import logging
from datetime import datetime
from database.database import db
from services.pool_service import pool_service
from utils.audit_logger import audit_log
from config import CLAIM_CODE_PREFIX

logger = logging.getLogger('tnnr.services.claim')

class ClaimService:
    """Handles claim code redemption and account delivery."""
    
    @staticmethod
    def validate_claim_code(claim_code: str, telegram_id: int) -> dict:
        """
        Validate claim code ownership and status.
        Returns: {'valid': bool, 'message': str, 'winner': dict}
        """
        try:
            # Check code format
            if not claim_code.startswith(CLAIM_CODE_PREFIX):
                return {
                    'valid': False,
                    'message': 'Invalid claim code format',
                    'winner': None
                }
            
            # Check if code exists
            winner = db.execute_one(
                """SELECT id, telegram_id, username, display_name, prize, claimed_status, giveaway_id
                   FROM winners WHERE claim_code = ?""",
                (claim_code,)
            )
            
            if not winner:
                return {
                    'valid': False,
                    'message': 'Claim code not found',
                    'winner': None
                }
            
            # Check ownership
            if winner[1] != telegram_id:
                logger.warning(f"Ownership validation failed: {telegram_id} tried to claim code for {winner[1]}")
                return {
                    'valid': False,
                    'message': 'This claim code belongs to another account',
                    'winner': None
                }
            
            # Check if already claimed
            if winner[5] == 1:
                return {
                    'valid': False,
                    'message': 'This claim code has already been redeemed',
                    'winner': None
                }
            
            return {
                'valid': True,
                'message': 'Claim code valid',
                'winner': {
                    'id': winner[0],
                    'telegram_id': winner[1],
                    'username': winner[2],
                    'display_name': winner[3],
                    'prize': winner[4],
                    'giveaway_id': winner[6]
                }
            }
        except Exception as e:
            logger.error(f"Claim validation failed: {e}")
            return {
                'valid': False,
                'message': f'Validation error: {str(e)}',
                'winner': None
            }
    
    @staticmethod
    def redeem_claim_code(claim_code: str, telegram_id: int, username: str) -> dict:
        """
        Redeem a claim code and deliver accounts.
        Returns: {'success': bool, 'accounts': list, 'message': str}
        """
        try:
            # Validate first
            validation = ClaimService.validate_claim_code(claim_code, telegram_id)
            
            if not validation['valid']:
                return {
                    'success': False,
                    'accounts': [],
                    'message': validation['message']
                }
            
            winner = validation['winner']
            prize_text = winner['prize']
            
            # Parse prize (e.g., "5 Accounts")
            try:
                account_count = int(prize_text.split()[0])
            except (ValueError, IndexError):
                return {
                    'success': False,
                    'accounts': [],
                    'message': 'Invalid prize format'
                }
            
            # Get accounts
            accounts = pool_service.get_available_accounts(account_count)
            
            if len(accounts) < account_count:
                # Revert reserved if partial
                account_ids = [acc[0] for acc in accounts]
                if account_ids:
                    pool_service.revert_reserved_accounts(account_ids)
                
                return {
                    'success': False,
                    'accounts': [],
                    'message': 'Not enough accounts available in inventory'
                }
            
            # Mark accounts as delivered
            account_ids = [acc[0] for acc in accounts]
            if not pool_service.mark_accounts_delivered(claim_code, account_ids):
                pool_service.revert_reserved_accounts(account_ids)
                return {
                    'success': False,
                    'accounts': [],
                    'message': 'Failed to deliver accounts'
                }
            
            # Update winner
            db.execute(
                """UPDATE winners 
                   SET claimed_status = 1, claimed_at = ? 
                   WHERE claim_code = ?""",
                (datetime.now(), claim_code)
            )
            db.commit()
            
            # Create redemption record
            db.execute(
                """INSERT INTO redemptions 
                   (claim_code, telegram_id, prize, accounts_delivered, redeemed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (claim_code, telegram_id, prize_text, account_count, datetime.now())
            )
            db.commit()
            
            # Audit
            audit_log.log(
                action='CLAIM_REDEEMED',
                actor_id=telegram_id,
                actor_name=username,
                details=f"Prize: {prize_text}, Code: {claim_code}",
                result='SUCCESS'
            )
            
            logger.info(f"Claim redeemed: {claim_code} by {telegram_id}")
            
            # Format accounts for response
            formatted_accounts = [f"{acc[1]}:{acc[2]}" for acc in accounts]
            
            return {
                'success': True,
                'accounts': formatted_accounts,
                'message': 'Prize delivered successfully'
            }
        except Exception as e:
            logger.error(f"Claim redemption failed: {e}")
            return {
                'success': False,
                'accounts': [],
                'message': f'Redemption error: {str(e)}'
            }
    
    @staticmethod
    def get_claim_status(claim_code: str) -> dict:
        """Get status of a claim code."""
        try:
            winner = db.execute_one(
                """SELECT claim_code, telegram_id, username, prize, claimed_status, claimed_at
                   FROM winners WHERE claim_code = ?""",
                (claim_code,)
            )
            
            if not winner:
                return {'found': False}
            
            return {
                'found': True,
                'code': winner[0],
                'winner_id': winner[1],
                'winner': winner[2],
                'prize': winner[3],
                'claimed': winner[4] == 1,
                'claimed_at': winner[5]
            }
        except Exception as e:
            logger.error(f"Failed to get claim status: {e}")
            return {'found': False}

claim_service = ClaimService()
