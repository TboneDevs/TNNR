import logging
import sys
from logging.handlers import RotatingFileHandler
from config import RAILWAY_VOLUME_MOUNT_PATH
import os

def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """
    Setup logging for the application.
    Logs to console and file.
    """
    logger = logging.getLogger("tnnr")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler (Railway volume)
    log_path = os.path.join(RAILWAY_VOLUME_MOUNT_PATH, "logs")
    os.makedirs(log_path, exist_ok=True)
    
    file_handler = RotatingFileHandler(
        os.path.join(log_path, "tnnr.log"),
        maxBytes=10485760,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    file_handler.setFormatter(console_format)
    logger.addHandler(file_handler)
    
    return logger

def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(f"tnnr.{name}")
