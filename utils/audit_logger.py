import logging
from datetime import datetime
from database.database import db

logger = logging.getLogger('tnnr.audit')

class AuditLogger:
    """Handles audit logging for all critical actions."""
    
    @staticmethod
    def log(action: str, actor_id: int = None, actor_name: str = None, 
            details: str = None, result: str = None):
        """Log an action to audit trail."""
        try:
            cursor = db.execute(
                """INSERT INTO audit_logs 
                   (timestamp, actor_id, actor_name, action, details, result)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (datetime.now(), actor_id, actor_name, action, details, result)
            )
            db.commit()
            logger.info(f"Audit: {action} by {actor_name} ({actor_id})")
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
    
    @staticmethod
    def get_logs(limit: int = 100, offset: int = 0):
        """Retrieve audit logs."""
        return db.execute_all(
            """SELECT * FROM audit_logs 
               ORDER BY timestamp DESC 
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )
    
    @staticmethod
    def export_logs(start_date: datetime = None, end_date: datetime = None):
        """Export audit logs for date range."""
        if start_date and end_date:
            return db.execute_all(
                """SELECT * FROM audit_logs 
                   WHERE timestamp BETWEEN ? AND ?
                   ORDER BY timestamp DESC""",
                (start_date, end_date)
            )
        return db.execute_all(
            "SELECT * FROM audit_logs ORDER BY timestamp DESC"
        )

# Global audit logger
audit_log = AuditLogger()
