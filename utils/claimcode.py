import re
import secrets
import string
import unicodedata

from config import CLAIM_CODE_LENGTH, CLAIM_CODE_PREFIX


def _normalized_prefix() -> str:
    return str(CLAIM_CODE_PREFIX).strip().upper()


def _strip_hidden(value: str) -> str:
    return (
        unicodedata.normalize("NFKC", value)
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )


def claim_code_search_key(code: str) -> str:
    """Return a punctuation-insensitive lookup key for stored/user codes."""
    if not isinstance(code, str):
        return ""
    cleaned = _strip_hidden(code).strip().upper()
    cleaned = cleaned.replace("_", "-")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.replace("-", "")


def normalize_claim_code(code: str) -> str | None:
    """Return the canonical PREFIX-SUFFIX form for a safely formatted code.

    Accepts harmless Telegram/copy-paste variations such as lowercase text,
    underscores, spaces around separators, missing hyphen after the prefix, and
    hidden zero-width characters.  It does not require the DB to store exactly
    this spelling; service lookups also compare punctuation-insensitive keys.
    """
    if not isinstance(code, str):
        return None

    cleaned = _strip_hidden(code).strip().upper()
    cleaned = cleaned.replace("_", "-")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)

    prefix = _normalized_prefix()
    if cleaned.startswith(prefix) and not cleaned.startswith(prefix + "-"):
        cleaned = prefix + "-" + cleaned[len(prefix):]

    pattern = re.compile(rf"^{re.escape(prefix)}-([A-Z0-9]{{{CLAIM_CODE_LENGTH}}})$")
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
