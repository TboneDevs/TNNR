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

SCHEMA_VERSION = 6


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
        if 2 not in applied:
            self._migration_002(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (2, datetime.utcnow().isoformat()),
            )
            conn.commit()
            logger.info("Applied migration 002")
            applied.add(2)
        if 3 not in applied:
            self._migration_003(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (3, datetime.utcnow().isoformat()),
            )
            conn.commit()
            logger.info("Applied migration 003")
            applied.add(3)
        if 4 not in applied:
            self._migration_004(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (4, datetime.utcnow().isoformat()),
            )
            conn.commit()
            logger.info("Applied migration 004")
            applied.add(4)
        if 5 not in applied:
            self._migration_005(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (5, datetime.utcnow().isoformat()),
            )
            conn.commit()
            logger.info("Applied migration 005")
            applied.add(5)
        if 6 not in applied:
            self._migration_006(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (6, datetime.utcnow().isoformat()),
            )
            conn.commit()
            logger.info("Applied migration 006")

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
                created_by_admin_id INTEGER,
                active_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT
            );

            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                display_name TEXT,
                message_id INTEGER,
                entry_text TEXT,
                submitted_answer TEXT,
                entry_number INTEGER,
                guessed_number INTEGER,
                source_type TEXT,
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

    def _migration_002(self, conn: sqlite3.Connection):
        """Backfill announcement metadata columns for existing Railway databases."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(giveaways)")}
        if "announcement_channel_id" not in columns:
            conn.execute("ALTER TABLE giveaways ADD COLUMN announcement_channel_id INTEGER")
        if "announcement_message_id" not in columns:
            conn.execute("ALTER TABLE giveaways ADD COLUMN announcement_message_id INTEGER")
        if "discussion_group_id" not in columns:
            conn.execute("ALTER TABLE giveaways ADD COLUMN discussion_group_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_announcement_channel ON giveaways(announcement_channel_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_announcement_message ON giveaways(announcement_message_id)")

    def _migration_003(self, conn: sqlite3.Connection):
        """Add discussion-group and entry metadata used by giveaway access repair."""
        giveaway_columns = {row[1] for row in conn.execute("PRAGMA table_info(giveaways)")}
        if "discussion_group_id" not in giveaway_columns:
            conn.execute("ALTER TABLE giveaways ADD COLUMN discussion_group_id INTEGER")
        if "created_by_admin_id" not in giveaway_columns:
            conn.execute("ALTER TABLE giveaways ADD COLUMN created_by_admin_id INTEGER")
        if "active_status" not in giveaway_columns:
            conn.execute("ALTER TABLE giveaways ADD COLUMN active_status TEXT")
        conn.execute("UPDATE giveaways SET created_by_admin_id = COALESCE(created_by_admin_id, created_by)")
        conn.execute("UPDATE giveaways SET active_status = COALESCE(active_status, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_discussion_group ON giveaways(discussion_group_id)")

        entry_columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
        for column, ddl in {
            "first_name": "ALTER TABLE entries ADD COLUMN first_name TEXT",
            "last_name": "ALTER TABLE entries ADD COLUMN last_name TEXT",
            "submitted_answer": "ALTER TABLE entries ADD COLUMN submitted_answer TEXT",
            "guessed_number": "ALTER TABLE entries ADD COLUMN guessed_number INTEGER",
            "source_type": "ALTER TABLE entries ADD COLUMN source_type TEXT",
        }.items():
            if column not in entry_columns:
                conn.execute(ddl)
        conn.execute("UPDATE entries SET submitted_answer = COALESCE(submitted_answer, entry_text)")
        conn.execute("UPDATE entries SET guessed_number = COALESCE(guessed_number, entry_number)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_source_type ON entries(source_type)")

    def _migration_004(self, conn: sqlite3.Connection):
        """Add direct owed-account delivery tables and migrate unredeemed winners."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS account_entitlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                owed_amount INTEGER NOT NULL DEFAULT 0,
                delivered_amount INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                source_type TEXT,
                giveaway_id TEXT,
                prize TEXT,
                created_by_admin_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS account_delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount_requested INTEGER NOT NULL DEFAULT 0,
                amount_delivered INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                reason TEXT,
                trigger TEXT,
                account_ids TEXT,
                entitlement_ids TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_account_entitlements_user_status
                ON account_entitlements(telegram_id, status);
            CREATE INDEX IF NOT EXISTS idx_account_entitlements_giveaway
                ON account_entitlements(giveaway_id);
            CREATE INDEX IF NOT EXISTS idx_account_delivery_logs_user
                ON account_delivery_logs(telegram_id);
            """
        )

        import re
        migrated_at = datetime.utcnow().isoformat()
        rows = conn.execute(
            """SELECT id, telegram_id, prize, giveaway_id, created_at
               FROM winners
               WHERE COALESCE(claimed_status, 0) = 0"""
        ).fetchall()
        for row in rows:
            prize = row[2] or ""
            match = re.search(r"\b(\d+)\b", prize)
            if match:
                amount = int(match.group(1))
            elif re.search(r"\baccount\b", prize, re.IGNORECASE):
                amount = 1
            else:
                continue
            if amount <= 0:
                continue
            existing = conn.execute(
                """SELECT id FROM account_entitlements
                   WHERE telegram_id = ? AND giveaway_id = ? AND source_type = 'legacy_claim_migration'
                   LIMIT 1""",
                (row[1], row[3]),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """INSERT INTO account_entitlements
                   (telegram_id, owed_amount, delivered_amount, status, source_type, giveaway_id, prize, created_at, updated_at)
                   VALUES (?, ?, 0, 'pending', 'legacy_claim_migration', ?, ?, ?, ?)""",
                (row[1], amount, row[3], prize, row[4] or migrated_at, migrated_at),
            )


    def _migration_005(self, conn: sqlite3.Connection):
        """Add persistent bonus claim cooldown and delivery tracking."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bonus_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                account_id INTEGER,
                status TEXT NOT NULL DEFAULT 'in_progress',
                claimed_at TEXT,
                failed_at TEXT,
                failure_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_bonus_claims_user_status
                ON bonus_claims(telegram_id, status);
            CREATE INDEX IF NOT EXISTS idx_bonus_claims_claimed_at
                ON bonus_claims(claimed_at);
            CREATE INDEX IF NOT EXISTS idx_bonus_claims_account
                ON bonus_claims(account_id);
            """
        )
    def _migration_006(self, conn: sqlite3.Connection):
        """Add free-credit profile, bet, and game tracking tables."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS credit_profiles (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT,
                total_won INTEGER NOT NULL DEFAULT 0,
                total_claimed INTEGER NOT NULL DEFAULT 0,
                current_bet INTEGER NOT NULL DEFAULT 1,
                slots_played INTEGER NOT NULL DEFAULT 0,
                coinflips_played INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS credit_game_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                command TEXT NOT NULL,
                amount_wagered INTEGER NOT NULL DEFAULT 0,
                amount_won INTEGER NOT NULL DEFAULT 0,
                amount_lost INTEGER NOT NULL DEFAULT 0,
                new_balance INTEGER NOT NULL DEFAULT 0,
                result TEXT,
                details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_credit_profiles_total_won
                ON credit_profiles(total_won DESC);
            CREATE INDEX IF NOT EXISTS idx_credit_profiles_balance
                ON credit_profiles(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_credit_game_logs_user
                ON credit_game_logs(telegram_id, created_at);
            """
        )
        now = datetime.utcnow().isoformat()
        rows = conn.execute(
            """SELECT telegram_id, COALESCE(SUM(owed_amount), 0), COALESCE(SUM(delivered_amount), 0)
               FROM account_entitlements
               GROUP BY telegram_id"""
        ).fetchall()
        for telegram_id, total_won, total_claimed in rows:
            if telegram_id is None:
                continue
            conn.execute(
                """INSERT INTO credit_profiles
                   (telegram_id, total_won, total_claimed, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                       total_won = MAX(total_won, excluded.total_won),
                       total_claimed = MAX(total_claimed, excluded.total_claimed),
                       updated_at = excluded.updated_at""",
                (telegram_id, int(total_won or 0), int(total_claimed or 0), now, now),
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
                "account_entitlements", "account_delivery_logs", "bonus_claims",
                "credit_profiles", "credit_game_logs",
            }
            existing = {row[0] for row in self.execute_all("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = required_tables - existing
            if missing:
                logger.error("Missing database tables: %s", ", ".join(sorted(missing)))
                return False
            giveaway_columns = {row[1] for row in self.execute_all("PRAGMA table_info(giveaways)")}
            required_giveaway_columns = {"announcement_channel_id", "announcement_message_id", "discussion_group_id", "created_by_admin_id", "active_status"}
            missing_columns = required_giveaway_columns - giveaway_columns
            if missing_columns:
                logger.error("Missing giveaway metadata columns: %s", ", ".join(sorted(missing_columns)))
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
