import logging
from datetime import datetime
from database.database import db
from services.pool_service import pool_service
from utils.audit_logger import audit_log
from utils.claimcode import normalize_claim_code

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
            canonical_code = normalize_claim_code(claim_code)
            if not canonical_code:
                return {
                    'valid': False,
                    'message': 'Invalid claim code format',
                    'winner': None
                }

            winner = db.execute_one(
                """SELECT id, claim_code, telegram_id, username, display_name, prize, claimed_status, giveaway_id
                   FROM winners WHERE UPPER(claim_code) = ?""",
                (canonical_code,)
            )

            if not winner:
                return {
                    'valid': False,
                    'message': 'Claim code not found',
                    'winner': None
                }

            if winner[2] != telegram_id:
                logger.warning(f"Ownership validation failed: {telegram_id} tried to claim code for {winner[2]}")
                return {
                    'valid': False,
                    'message': 'This claim code belongs to another account',
                    'winner': None
                }

            if winner[6] == 1:
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
                    'claim_code': winner[1],
                    'telegram_id': winner[2],
                    'username': winner[3],
                    'display_name': winner[4],
                    'prize': winner[5],
                    'giveaway_id': winner[7]
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
            stored_claim_code = winner['claim_code']
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
            if not pool_service.mark_accounts_delivered(stored_claim_code, account_ids):
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
                   WHERE id = ?""",
                (datetime.now(), winner['id'])
            )
            db.commit()
            
            # Create redemption record
            db.execute(
                """INSERT INTO redemptions 
                   (claim_code, telegram_id, prize, accounts_delivered, redeemed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (stored_claim_code, telegram_id, prize_text, account_count, datetime.now())
            )
            db.execute(
                """UPDATE claim_codes
                   SET status = 'redeemed', redeemed_at = ?
                   WHERE UPPER(code) = UPPER(?)""",
                (datetime.now(), stored_claim_code)
            )
            db.commit()
            
            # Audit
            audit_log.log(
                action='CLAIM_REDEEMED',
                actor_id=telegram_id,
                actor_name=username,
                details=f"Prize: {prize_text}, Code: {stored_claim_code}",
                result='SUCCESS'
            )
            
            logger.info(f"Claim redeemed: {stored_claim_code} by {telegram_id}")
            
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
        """Get status of a claim code using the same normalization as redemption."""
        try:
            canonical_code = normalize_claim_code(claim_code)
            if not canonical_code:
                return {'found': False}
            winner = db.execute_one(
                """SELECT claim_code, telegram_id, username, prize, claimed_status, claimed_at
                   FROM winners WHERE UPPER(claim_code) = ?""",
                (canonical_code,)
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
