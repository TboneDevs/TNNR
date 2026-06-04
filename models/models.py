from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class User:
    """User model."""
    telegram_id: int
    username: Optional[str] = None
    display_name: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

@dataclass
class Giveaway:
    """Giveaway model."""
    giveaway_id: str
    type: str
    prize: str
    status: str
    created_by: int
    created_at: datetime
    announcement_message_id: Optional[int] = None
    announcement_channel_id: Optional[int] = None
    discussion_group_id: Optional[int] = None
    hidden_answer: Optional[str] = None
    winning_number: Optional[int] = None
    min_number: Optional[int] = None
    max_number: Optional[int] = None
    ended_at: Optional[datetime] = None

@dataclass
class Entry:
    """Giveaway entry model."""
    giveaway_id: str
    telegram_id: int
    username: Optional[str] = None
    display_name: Optional[str] = None
    message_id: Optional[int] = None
    entry_text: Optional[str] = None
    entry_number: Optional[int] = None
    timestamp: Optional[datetime] = None

@dataclass
class Winner:
    """Winner model."""
    claim_code: str
    giveaway_id: str
    telegram_id: int
    prize: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    claimed_status: int = 0
    created_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None

@dataclass
class Account:
    """Account pool model."""
    email: str
    password: str
    status: str
    uploaded_by: Optional[int] = None
    uploaded_at: Optional[datetime] = None
    assigned_claim_code: Optional[str] = None
    assigned_user: Optional[int] = None
    reserved_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None

@dataclass
class AuditLog:
    """Audit log model."""
    action: str
    timestamp: Optional[datetime] = None
    actor_id: Optional[int] = None
    actor_name: Optional[str] = None
    details: Optional[str] = None
    result: Optional[str] = None
