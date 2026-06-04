import logging
from datetime import datetime, timedelta
from database.database import db
from config import RESERVED_ACCOUNT_TIMEOUT_HOURS

logger = logging.getLogger('tnnr.recovery')

class RecoveryManager:
    """Handles startup recovery and consistency checks."""
    
    @staticmethod
    def recovery_orphaned_reserved_accounts():
        """Return reserved accounts to available if delivery never completed."""
        try:
            timeout_hours = RESERVED_ACCOUNT_TIMEOUT_HOURS
            cutoff_time = datetime.now() - timedelta(hours=timeout_hours)
            
            # Find reserved accounts that were reserved too long ago
            orphaned = db.execute_all(
                """SELECT id FROM account_pool 
                   WHERE status = 'reserved' 
                   AND reserved_at < ?""",
                (cutoff_time,)
            )
            
            for row in orphaned:
                db.execute(
                    "UPDATE account_pool SET status = 'available', reserved_at = NULL WHERE id = ?",
                    (row[0],)
                )
                db.commit()
                logger.info(f"Recovered orphaned reserved account: {row[0]}")
            
            if orphaned:
                logger.info(f"Recovered {len(orphaned)} orphaned reserved accounts")
        except Exception as e:
            logger.error(f"Orphaned account recovery failed: {e}")
    
    @staticmethod
    def check_database_consistency():
        """Check for orphaned records and inconsistencies."""
        try:
            issues = []
            
            # Check for winners without giveaways
            orphaned_winners = db.execute_all(
                """SELECT id, giveaway_id FROM winners 
                   WHERE giveaway_id NOT IN (SELECT giveaway_id FROM giveaways)"""
            )
            if orphaned_winners:
                issues.append(f"Found {len(orphaned_winners)} orphaned winners")
                logger.warning(f"Orphaned winners detected: {len(orphaned_winners)}")
            
            # Check for entries without giveaways
            orphaned_entries = db.execute_all(
                """SELECT id, giveaway_id FROM entries 
                   WHERE giveaway_id NOT IN (SELECT giveaway_id FROM giveaways)"""
            )
            if orphaned_entries:
                issues.append(f"Found {len(orphaned_entries)} orphaned entries")
                logger.warning(f"Orphaned entries detected: {len(orphaned_entries)}")
            
            # Check for unclaimed winners
            unclaimed = db.execute_all(
                """SELECT COUNT(*) as count FROM winners 
                   WHERE claimed_status = 0 
                   AND created_at < datetime('now', '-7 days')"""
            )
            if unclaimed and unclaimed[0][0] > 0:
                logger.info(f"Found {unclaimed[0][0]} unclaimed winners older than 7 days")
            
            return issues
        except Exception as e:
            logger.error(f"Database consistency check failed: {e}")
            return []
    
    @staticmethod
    def verify_claim_integrity():
        """Verify claim codes and redemptions are consistent."""
        try:
            # Check for duplicate claim codes
            duplicates = db.execute_all(
                """SELECT code, COUNT(*) as count FROM claim_codes 
                   GROUP BY code HAVING count > 1"""
            )
            if duplicates:
                logger.error(f"Found duplicate claim codes: {duplicates}")
                return False
            
            # Check for unredeemed claims from deleted winners
            orphaned_claims = db.execute_all(
                """SELECT id FROM claim_codes 
                   WHERE winner_id NOT IN (SELECT id FROM winners)"""
            )
            if orphaned_claims:
                logger.warning(f"Found {len(orphaned_claims)} orphaned claim codes")
            
            logger.info("Claim integrity verified")
            return True
        except Exception as e:
            logger.error(f"Claim integrity check failed: {e}")
            return False
    
    @staticmethod
    def startup_recovery():
        """Run all recovery procedures on startup."""
        logger.info("Starting recovery procedures...")
        
        try:
            RecoveryManager.recovery_orphaned_reserved_accounts()
            consistency_issues = RecoveryManager.check_database_consistency()
            claim_integrity = RecoveryManager.verify_claim_integrity()
            
            if consistency_issues:
                logger.warning(f"Found {len(consistency_issues)} consistency issues")
            
            if not claim_integrity:
                logger.error("Claim integrity check failed")
                return False
            
            logger.info("Recovery procedures completed successfully")
            return True
        except Exception as e:
            logger.critical(f"Startup recovery failed: {e}")
            return False

recovery_manager = RecoveryManager()
