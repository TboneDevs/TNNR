import re
import secrets
import string
import unicodedata

from config import CLAIM_CODE_LENGTH, CLAIM_CODE_PREFIX


def _normalized_prefix() -> str:
    return str(CLAIM_CODE_PREFIX).strip().upper()


def normalize_claim_code(code: str) -> str | None:
    """Return the canonical PREFIX-SUFFIX form for user-entered claim codes.

    Telegram users commonly copy codes with lowercase letters, leading/trailing
    whitespace, zero-width characters, or spaces around the hyphen.  Redemption
    lookups must accept those harmless variations while still rejecting malformed
    values.
    """
    if not isinstance(code, str):
        return None

    cleaned = unicodedata.normalize("NFKC", code)
    cleaned = cleaned.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    cleaned = cleaned.strip().upper()
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)

    prefix = _normalized_prefix()
    pattern = re.compile(rf"^{re.escape(prefix)}(?:-|\s)?([A-Z0-9]{{{CLAIM_CODE_LENGTH}}})$")
    match = pattern.fullmatch(cleaned)
    if not match:
        return None
    return f"{prefix}-{match.group(1)}"


def generate_claim_code() -> str:
    """Generate a secure, random canonical claim code.

    Format: PREFIX-XXXXXX (e.g., CPM-A1B2C3).  Generated codes are uppercase
    so the value sent to winners, stored in the DB, and shown in admin logs all
    share one canonical representation.
    """
    characters = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(characters) for _ in range(CLAIM_CODE_LENGTH))
    return f"{_normalized_prefix()}-{suffix}"


def validate_claim_code_format(code: str) -> bool:
    """Validate claim code format after safe user-input normalization."""
    return normalize_claim_code(code) is not None
