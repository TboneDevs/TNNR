#!/usr/bin/env python3
"""
TNNR - Enterprise Telegram Giveaway Automation System
Version: 2.0 Enterprise
Target: Railway Deployment
""" 

import sys

from telegram.ext import Application

from config import ADMIN_IDS, BOT_TOKEN, LOG_LEVEL
from database.database import db
from handlers.admin_handlers import register_admin_handlers
from handlers.claim_handlers import register_claim_handlers
from handlers.giveaway_handlers import register_giveaway_handlers
from utils.logging_utils import setup_logging
from utils.recovery_manager import recovery_manager

logger = setup_logging(LOG_LEVEL)


def validate_environment() -> bool:
    """Validate critical deployment settings and log actionable warnings."""
    ok = True
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment variables")
        ok = False
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty; admin commands will be unavailable")
    return ok


def build_application() -> Application:
    """Create and configure the Telegram application."""
    application = Application.builder().token(BOT_TOKEN).build()
    register_admin_handlers(application)
    register_claim_handlers(application)
    register_giveaway_handlers(application)
    return application


def main():
    """Start the Telegram bot."""
    try:
        logger.info("=" * 50)
        logger.info("TNNR Enterprise Giveaway System v2.0")
        logger.info("=" * 50)

        if not validate_environment():
            sys.exit(1)

        logger.info("Initializing database...")
        db.initialize()

        logger.info("Running startup validation...")
        if not db.validate_startup():
            logger.error("Startup validation failed")
            sys.exit(1)

        logger.info("Running startup recovery...")
        if not recovery_manager.startup_recovery():
            logger.warning("Startup recovery reported issues; bot will continue with affected features logged")

        logger.info("Registering Telegram handlers...")
        app = build_application()

        logger.info("Bot initialization complete; starting polling")
        app.run_polling(allowed_updates=None)

    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.critical("Fatal error during startup: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
