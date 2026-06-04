import logging
from datetime import datetime
from database.database import db
from utils.validators import validate_account_format, validate_email
from utils.audit_logger import audit_log
from config import LOW_STOCK_ALERT_AMOUNT

logger = logging.getLogger('tnnr.services.pool')

class PoolService:
    """Handles account pool management, uploads, and inventory."""
    
    @staticmethod
    def import_accounts(lines: list, admin_id: int, admin_name: str) -> dict:
        """
        Import accounts from list of email:password lines.
        Returns: {'added': int, 'duplicates': int, 'invalid': int, 'total': int}
        """
        try:
            added = 0
            duplicates = 0
            invalid = 0
            
            for line in lines:
                is_valid, email, password = validate_account_format(line)
                
                if not is_valid:
                    invalid += 1
                    continue
                
                # Check if already exists
                existing = db.execute_one(
                    "SELECT id FROM account_pool WHERE email = ?",
                    (email,)
                )
                
                if existing:
                    duplicates += 1
                    continue
                
                # Add account
                db.execute(
                    """INSERT INTO account_pool 
                       (email, password, status, uploaded_by, uploaded_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (email, password, 'available', admin_id, datetime.now())
                )
                added += 1
            
            db.commit()
            
            # Get total
            total_result = db.execute_one(
                "SELECT COUNT(*) FROM account_pool WHERE status = 'available'"
            )
            total = total_result[0] if total_result else 0
            
            audit_log.log(
                action='POOL_UPLOAD',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Added: {added}, Duplicates: {duplicates}, Invalid: {invalid}",
                result='SUCCESS'
            )
            
            logger.info(f"Pool import complete: {added} added, {duplicates} duplicates, {invalid} invalid")
            return {
                'added': added,
                'duplicates': duplicates,
                'invalid': invalid,
                'total': total
            }
        except Exception as e:
            logger.error(f"Failed to import accounts: {e}")
            audit_log.log(
                action='POOL_UPLOAD',
                actor_id=admin_id,
                actor_name=admin_name,
                result=f'FAILED: {str(e)}'
            )
            return {'added': 0, 'duplicates': 0, 'invalid': 0, 'total': 0}
    
    @staticmethod
    def get_available_accounts(count: int) -> list:
        """
        Get available accounts and mark as reserved.
        Returns: list of (id, email, password)
        """
        try:
            accounts = db.execute_all(
                """SELECT id, email, password FROM account_pool 
                   WHERE status = 'available' LIMIT ?""",
                (count,)
            )
            
            if len(accounts) < count:
                logger.warning(f"Not enough accounts: requested {count}, available {len(accounts)}")
                return []
            
            # Mark as reserved
            for account in accounts:
                db.execute(
                    "UPDATE account_pool SET status = 'reserved', reserved_at = ? WHERE id = ?",
                    (datetime.now(), account[0])
                )
            
            db.commit()
            logger.info(f"Reserved {len(accounts)} accounts")
            return accounts
        except Exception as e:
            logger.error(f"Failed to get available accounts: {e}")
            return []
    
    @staticmethod
    def mark_accounts_delivered(claim_code: str, account_ids: list) -> bool:
        """Mark accounts as delivered for a claim."""
        try:
            for account_id in account_ids:
                db.execute(
                    """UPDATE account_pool 
                       SET status = 'delivered', delivered_at = ?, assigned_claim_code = ?
                       WHERE id = ?""",
                    (datetime.now(), claim_code, account_id)
                )
            
            db.commit()
            logger.info(f"Marked {len(account_ids)} accounts as delivered")
            return True
        except Exception as e:
            logger.error(f"Failed to mark accounts delivered: {e}")
            return False
    
    @staticmethod
    def revert_reserved_accounts(account_ids: list) -> bool:
        """Return reserved accounts to available."""
        try:
            for account_id in account_ids:
                db.execute(
                    """UPDATE account_pool 
                       SET status = 'available', reserved_at = NULL
                       WHERE id = ?""",
                    (account_id,)
                )
            
            db.commit()
            logger.info(f"Reverted {len(account_ids)} accounts to available")
            return True
        except Exception as e:
            logger.error(f"Failed to revert accounts: {e}")
            return False
    
    @staticmethod
    def get_pool_status() -> dict:
        """Get account pool statistics."""
        try:
            statuses = ['available', 'reserved', 'delivered', 'invalid', 'removed']
            status_counts = {}
            
            for status in statuses:
                result = db.execute_one(
                    "SELECT COUNT(*) FROM account_pool WHERE status = ?",
                    (status,)
                )
                status_counts[status] = result[0] if result else 0
            
            total = sum(status_counts.values())
            
            return {
                'available': status_counts['available'],
                'reserved': status_counts['reserved'],
                'delivered': status_counts['delivered'],
                'invalid': status_counts['invalid'],
                'removed': status_counts['removed'],
                'total': total
            }
        except Exception as e:
            logger.error(f"Failed to get pool status: {e}")
            return {}
    
    @staticmethod
    def check_low_stock() -> bool:
        """Check if stock is low and alert if needed."""
        try:
            result = db.execute_one(
                "SELECT COUNT(*) FROM account_pool WHERE status = 'available'"
            )
            available = result[0] if result else 0
            
            if available < LOW_STOCK_ALERT_AMOUNT:
                logger.warning(f"Low stock alert: {available} accounts available")
                audit_log.log(
                    action='LOW_STOCK_ALERT',
                    details=f"Available accounts: {available}",
                    result='ALERT'
                )
                return True
            
            return False
        except Exception as e:
            logger.error(f"Failed to check stock: {e}")
            return False
    
    @staticmethod
    def mark_invalid(email: str, admin_id: int, admin_name: str) -> bool:
        """Mark an account as invalid."""
        try:
            db.execute(
                "UPDATE account_pool SET status = 'invalid' WHERE email = ?",
                (email,)
            )
            db.commit()
            
            audit_log.log(
                action='ACCOUNT_MARKED_INVALID',
                actor_id=admin_id,
                actor_name=admin_name,
                details=f"Email: {email}",
                result='SUCCESS'
            )
            
            logger.info(f"Marked account as invalid: {email}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark invalid: {e}")
            return False

pool_service = PoolService()
