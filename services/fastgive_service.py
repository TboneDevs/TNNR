"""Service layer for 60-second /fastgive flash giveaways."""

from __future__ import annotations

import secrets
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from database.database import db

FASTGIVE_DURATION_SECONDS = 60
FASTGIVE_UPDATE_SECONDS = (50, 40, 30, 20, 10)

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.RLock()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _lock_for(giveaway_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(giveaway_id, threading.RLock())


def cleanup_lock(giveaway_id: str):
    with _LOCKS_GUARD:
        _LOCKS.pop(giveaway_id, None)


def new_fastgive_id() -> str:
    """Return a compact unique fast-giveaway ID."""
    return f"FG-{uuid.uuid4().hex[:8].upper()}"


def create_fast_giveaway(
    *,
    giveaway_id: str,
    prize: str,
    creator_id: int,
    creator_name: Optional[str],
    announcement_channel_id: int,
    announcement_message_id: int,
) -> Dict[str, Any]:
    now = _utcnow()
    end_at = now + timedelta(seconds=FASTGIVE_DURATION_SECONDS)
    try:
        db.execute(
            """INSERT INTO fast_giveaways
               (giveaway_id, prize, status, creator_id, creator_name,
                announcement_channel_id, announcement_message_id, start_at, end_at, created_at, updated_at)
               VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                giveaway_id,
                prize,
                creator_id,
                creator_name,
                announcement_channel_id,
                announcement_message_id,
                _iso(now),
                _iso(end_at),
                _iso(now),
                _iso(now),
            ),
        )
        db.commit()
        return {"success": True, "giveaway_id": giveaway_id, "start_at": _iso(now), "end_at": _iso(end_at)}
    except Exception as exc:
        db.rollback()
        return {"success": False, "message": str(exc)}


def get_giveaway(giveaway_id: str):
    return db.execute_one("SELECT * FROM fast_giveaways WHERE giveaway_id = ?", (giveaway_id,))


def entry_count(giveaway_id: str) -> int:
    row = db.execute_one("SELECT COUNT(*) FROM fast_giveaway_entries WHERE giveaway_id = ?", (giveaway_id,))
    return int(row[0] if row else 0)


def add_entry(
    giveaway_id: str,
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    display_name: Optional[str],
) -> Dict[str, Any]:
    """Add one entry, using DB uniqueness and a per-giveaway lock for race safety."""
    lock = _lock_for(giveaway_id)
    with lock:
        conn = db.connect()
        if conn.in_transaction:
            conn.commit()
        try:
            conn.execute("BEGIN IMMEDIATE")
            giveaway = conn.execute(
                "SELECT status, end_at FROM fast_giveaways WHERE giveaway_id = ?",
                (giveaway_id,),
            ).fetchone()
            if not giveaway:
                conn.rollback()
                return {"success": False, "status": "not_found", "message": "This giveaway was not found."}
            if giveaway[0] != "active" or (giveaway[1] and _iso(_utcnow()) >= giveaway[1]):
                conn.rollback()
                return {"success": False, "status": "closed", "message": "This giveaway is closed."}
            try:
                conn.execute(
                    """INSERT INTO fast_giveaway_entries
                       (giveaway_id, telegram_id, username, first_name, last_name, display_name, entered_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (giveaway_id, telegram_id, username, first_name, last_name, display_name, _iso(_utcnow())),
                )
            except Exception as exc:
                conn.rollback()
                if "UNIQUE" in str(exc).upper():
                    return {"success": False, "status": "duplicate", "message": "You already entered this giveaway."}
                raise
            count = conn.execute(
                "SELECT COUNT(*) FROM fast_giveaway_entries WHERE giveaway_id = ?",
                (giveaway_id,),
            ).fetchone()[0]
            conn.commit()
            return {"success": True, "status": "entered", "entry_count": int(count)}
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            return {"success": False, "status": "error", "message": str(exc)}


def close_for_finalization(giveaway_id: str) -> Dict[str, Any]:
    """Atomically lock a giveaway for one finalizer and return entries."""
    lock = _lock_for(giveaway_id)
    with lock:
        conn = db.connect()
        if conn.in_transaction:
            conn.commit()
        try:
            conn.execute("BEGIN IMMEDIATE")
            giveaway = conn.execute("SELECT * FROM fast_giveaways WHERE giveaway_id = ?", (giveaway_id,)).fetchone()
            if not giveaway:
                conn.rollback()
                return {"success": False, "status": "not_found"}
            if giveaway["status"] != "active":
                conn.rollback()
                return {"success": False, "status": "already_finalized", "giveaway": dict(giveaway)}
            conn.execute(
                "UPDATE fast_giveaways SET status = 'finalizing', updated_at = ? WHERE giveaway_id = ? AND status = 'active'",
                (_iso(_utcnow()), giveaway_id),
            )
            entries = conn.execute(
                """SELECT telegram_id, username, first_name, last_name, display_name, entered_at
                   FROM fast_giveaway_entries WHERE giveaway_id = ? ORDER BY entered_at, id""",
                (giveaway_id,),
            ).fetchall()
            conn.commit()
            return {"success": True, "giveaway": dict(giveaway), "entries": [dict(row) for row in entries]}
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            return {"success": False, "status": "error", "message": str(exc)}


def choose_winner(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    return secrets.choice(entries)


def mark_cancelled(giveaway_id: str, total_entries: int = 0) -> None:
    now = _iso(_utcnow())
    db.execute(
        """UPDATE fast_giveaways
           SET status = 'cancelled', end_at = COALESCE(end_at, ?), finalized_at = ?, total_entries = ?, updated_at = ?
           WHERE giveaway_id = ?""",
        (now, now, int(total_entries), now, giveaway_id),
    )
    db.commit()
    cleanup_lock(giveaway_id)


def mark_ended(giveaway_id: str, winner: Dict[str, Any], total_entries: int) -> None:
    now = _iso(_utcnow())
    db.execute(
        """UPDATE fast_giveaways
           SET status = 'ended', finalized_at = ?, total_entries = ?,
               winner_telegram_id = ?, winner_username = ?, winner_display_name = ?, updated_at = ?
           WHERE giveaway_id = ?""",
        (
            now,
            int(total_entries),
            winner.get("telegram_id"),
            winner.get("username"),
            winner.get("display_name") or winner.get("first_name") or winner.get("last_name"),
            now,
            giveaway_id,
        ),
    )
    db.commit()
    cleanup_lock(giveaway_id)


def list_active_expired() -> list[dict]:
    now = _iso(_utcnow())
    rows = db.execute_all("SELECT * FROM fast_giveaways WHERE status = 'active' AND end_at <= ?", (now,))
    return [dict(row) for row in rows]
