import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(value.strip()) for value in os.getenv("ADMIN_IDS", "").split(",") if value.strip()]
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("ANNOUNCEMENT_CHANNEL_ID", "-1003846885691"))
DISCUSSION_GROUP_ID = int(os.getenv("DISCUSSION_GROUP_ID", "-1003994249946"))
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", 0))

# Giveaway Settings
LOW_STOCK_ALERT_AMOUNT = int(os.getenv("LOW_STOCK_ALERT_AMOUNT", "25"))
CLAIM_CODE_PREFIX = os.getenv("CLAIM_CODE_PREFIX", "CPM")
CLAIM_CODE_LENGTH = int(os.getenv("CLAIM_CODE_LENGTH", "6"))

# Backup Settings
BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "24"))
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Railway
RAILWAY_VOLUME_MOUNT_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(RAILWAY_VOLUME_MOUNT_PATH, "giveaways.db"))
EXPORTS_PATH = os.path.join(RAILWAY_VOLUME_MOUNT_PATH, "exports")
BACKUPS_PATH = os.path.join(RAILWAY_VOLUME_MOUNT_PATH, "backups")

# Reserved account cleanup threshold (hours)
RESERVED_ACCOUNT_TIMEOUT_HOURS = int(os.getenv("RESERVED_ACCOUNT_TIMEOUT_HOURS", "24"))
