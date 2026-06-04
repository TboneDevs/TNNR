"""Validation helpers shared by giveaway and pool services."""

import re
from typing import Optional, Tuple

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def validate_number(value: str, min_num: int, max_num: int) -> Tuple[bool, Optional[int]]:
    text = (value or "").strip()
    if not re.fullmatch(r"-?\d+", text):
        return False, None
    number = int(text)
    if number < min_num or number > max_num:
        return False, None
    return True, number


def validate_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def validate_account_format(line: str) -> Tuple[bool, Optional[str], Optional[str]]:
    text = (line or "").strip()
    if not text or ":" not in text:
        return False, None, None
    email, password = text.split(":", 1)
    email = email.strip()
    password = password.strip()
    if not email or not password or not validate_email(email):
        return False, None, None
    return True, email.lower(), password
