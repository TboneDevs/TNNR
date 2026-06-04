import logging
import shutil
import os
from datetime import datetime
from pathlib import Path
from config import BACKUPS_PATH, DATABASE_PATH, BACKUP_RETENTION_DAYS

logger = logging.getLogger('tnnr.backup')

class BackupManager:
    """Handles database backup and restoration."""
    
    def __init__(self):
        self.backup_path = BACKUPS_PATH
        Path(self.backup_path).mkdir(parents=True, exist_ok=True)
    
    def create_backup(self) -> str:
        """Create a timestamped database backup."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(
                self.backup_path,
                f"giveaways_backup_{timestamp}.db"
            )
            
            shutil.copy2(DATABASE_PATH, backup_file)
            logger.info(f"Backup created: {backup_file}")
            return backup_file
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return None
    
    def get_backups(self) -> list:
        """List all available backups."""
        try:
            backups = []
            for file in os.listdir(self.backup_path):
                if file.startswith("giveaways_backup_") and file.endswith(".db"):
                    backups.append(file)
            return sorted(backups, reverse=True)
        except Exception as e:
            logger.error(f"Failed to list backups: {e}")
            return []
    
    def cleanup_old_backups(self):
        """Remove backups older than retention period."""
        try:
            cutoff = datetime.now().timestamp() - (BACKUP_RETENTION_DAYS * 86400)
            
            for file in os.listdir(self.backup_path):
                file_path = os.path.join(self.backup_path, file)
                if os.path.getmtime(file_path) < cutoff:
                    os.remove(file_path)
                    logger.info(f"Deleted old backup: {file}")
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
    
    def restore_backup(self, backup_file: str) -> bool:
        """Restore database from backup."""
        try:
            full_path = os.path.join(self.backup_path, backup_file)
            if not os.path.exists(full_path):
                logger.error(f"Backup not found: {full_path}")
                return False
            
            # Create a safety backup of current database
            shutil.copy2(DATABASE_PATH, f"{DATABASE_PATH}.pre_restore")
            
            # Restore
            shutil.copy2(full_path, DATABASE_PATH)
            logger.info(f"Database restored from {backup_file}")
            return True
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False

backup_manager = BackupManager()
