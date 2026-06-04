"""SQLite database and migration management for TNNR.

The database lives on the Railway persistent volume by default.  The module
exposes a process-wide ``db`` instance used by the service layer and a
``Database`` class used by startup checks and tests.
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from config import BACKUPS_PATH, DATABASE_PATH, EXPORTS_PATH, RAILWAY_VOLUME_MOUNT_PATH

logger = logging.getLogger("tnnr.database")

SCHEMA_VERSION = 1


class Database:
    """Small SQLite helper with safe migrations and startup diagnostics."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or DATABASE_PATH
        self.connection: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self.connection is None:
            Path(os.path.dirname(os.path.abspath(self.path)) or ".").mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA busy_timeout = 5000")
        return self.connection

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def initialize(self):
        """Create storage directories and apply all schema migrations."""
        Path(RAILWAY_VOLUME_MOUNT_PATH).mkdir(parents=True, exist_ok=True)
        Path(EXPORTS_PATH).mkdir(parents=True, exist_ok=True)
        Path(BACKUPS_PATH).mkdir(parents=True, exist_ok=True)
        self.connect()
        self.run_migrations()
        logger.info("Database initialized at %s", self.path)

    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> sqlite3.Cursor:
        return self.connect().execute(query, tuple(params or ()))

    def execute_one(self, query: str, params: Optional[Iterable[Any]] = None):
        return self.execute(query, params).fetchone()

    def execute_all(self, query: str, params: Optional[Iterable[Any]] = None):
        return self.execute(query, params).fetchall()

    def commit(self):
        self.connect().commit()

    def rollback(self):
        self.connect().rollback()

    def transaction(self):
        return self.connect()

    def run_migrations(self):
        conn = self.connect()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )"""
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        if 1 not in applied:
            self._migration_001(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (1, datetime.utcnow().isoformat()),
            )
            conn.commit()
            logger.info("Applied migration 001")

    def _migration_001(self, conn: sqlite3.Connection):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                display_name TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                display_name TEXT,
                permission_level INTEGER DEFAULT 2,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL,
                prize TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                announcement_message_id INTEGER,
                announcement_channel_id INTEGER,
                discussion_group_id INTEGER,
                hidden_answer TEXT,
                winning_number INTEGER,
                min_number INTEGER,
                max_number INTEGER,
                created_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT
            );

            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT,
                message_id INTEGER,
                entry_text TEXT,
                entry_number INTEGER,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(giveaway_id, telegram_id)
            );

            CREATE TABLE IF NOT EXISTS winners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_code TEXT UNIQUE NOT NULL,
                giveaway_id TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT,
                prize TEXT NOT NULL,
                claimed_status INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                claimed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS claim_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                winner_id INTEGER,
                telegram_id INTEGER,
                prize TEXT,
                status TEXT DEFAULT 'unclaimed',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                redeemed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS account_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                uploaded_by INTEGER,
                uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                assigned_claim_code TEXT,
                assigned_user INTEGER,
                reserved_at TEXT,
                delivered_at TEXT
            );

            CREATE TABLE IF NOT EXISTS redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_code TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                accounts_delivered INTEGER NOT NULL DEFAULT 0,
                redeemed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                actor_id INTEGER,
                actor_name TEXT,
                action TEXT NOT NULL,
                details TEXT,
                result TEXT
            );

            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                source TEXT,
                message TEXT NOT NULL,
                details TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_giveaways_giveaway_id ON giveaways(giveaway_id);
            CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status);
            CREATE INDEX IF NOT EXISTS idx_giveaways_type ON giveaways(type);
            CREATE INDEX IF NOT EXISTS idx_entries_giveaway_id ON entries(giveaway_id);
            CREATE INDEX IF NOT EXISTS idx_entries_telegram_id ON entries(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_entries_timestamp ON entries(timestamp);
            CREATE INDEX IF NOT EXISTS idx_winners_claim_code ON winners(claim_code);
            CREATE INDEX IF NOT EXISTS idx_winners_telegram_id ON winners(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_account_pool_status ON account_pool(status);
            CREATE INDEX IF NOT EXISTS idx_account_pool_email ON account_pool(email);
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
            """
        )

    def validate_startup(self) -> bool:
        """Validate database and storage readiness without crashing the bot."""
        try:
            self.connect().execute("SELECT 1")
            Path(RAILWAY_VOLUME_MOUNT_PATH).mkdir(parents=True, exist_ok=True)
            if not os.access(RAILWAY_VOLUME_MOUNT_PATH, os.W_OK):
                logger.error("Volume path is not writable: %s", RAILWAY_VOLUME_MOUNT_PATH)
                return False
            required_tables = {
                "users", "admins", "giveaways", "entries", "winners", "claim_codes",
                "account_pool", "redemptions", "audit_logs", "system_logs", "schema_migrations",
            }
            existing = {row[0] for row in self.execute_all("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = required_tables - existing
            if missing:
                logger.error("Missing database tables: %s", ", ".join(sorted(missing)))
                return False
            return True
        except Exception as exc:
            logger.error("Startup database validation failed: %s", exc)
            return False

    def diagnostics(self) -> dict:
        try:
            migration = self.execute_one("SELECT MAX(version) FROM schema_migrations")
            pool = self.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'available'")
            active = self.execute_one("SELECT COUNT(*) FROM giveaways WHERE status = 'active'")
            return {
                "database": "connected",
                "volume": "writable" if os.access(RAILWAY_VOLUME_MOUNT_PATH, os.W_OK) else "not writable",
                "migration_version": migration[0] if migration else None,
                "available_accounts": pool[0] if pool else 0,
                "active_giveaways": active[0] if active else 0,
            }
        except Exception as exc:
            return {"database": "error", "error": str(exc)}


db = Database()
