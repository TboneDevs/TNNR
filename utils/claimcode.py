import re
import secrets
import string
import unicodedata

from config import CLAIM_CODE_LENGTH, CLAIM_CODE_PREFIX


DASH_TRANSLATION = str.maketrans({
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    "﹘": "-",
    "﹣": "-",
    "－": "-",
})


def _normalized_prefix() -> str:
    return str(CLAIM_CODE_PREFIX).strip().upper()


def _strip_hidden(value: str) -> str:
    """Normalize copy/paste artifacts without changing visible letters/digits."""
    normalized = unicodedata.normalize("NFKC", value).translate(DASH_TRANSLATION)
    return "".join(ch for ch in normalized if unicodedata.category(ch) not in {"Cf", "Cc"})


def _clean_for_matching(code: str) -> str:
    if not isinstance(code, str):
        return ""
    cleaned = _strip_hidden(code).strip().upper()
    cleaned = cleaned.replace("_", "-")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned


def claim_code_search_key(code: str) -> str:
    """Return a separator-insensitive lookup key for stored/user codes.

    This intentionally removes separators after Unicode normalization so these
    all match the same DB record: CPM-ABC123, CPMABC123, CPM_ABC123, and
    CPM–ABC123.  It is used only for lookup, not for displaying the code.
    """
    cleaned = _clean_for_matching(code)
    return re.sub(r"[^A-Z0-9]", "", cleaned)


def normalize_claim_code(code: str) -> str | None:
    """Return canonical PREFIX-SUFFIX for safely formatted user input.

    Accepts harmless Telegram/copy-paste variations such as lowercase text,
    underscores, spaces/newlines around separators, Unicode dash variants,
    missing hyphen after the prefix, non-breaking spaces, and hidden zero-width
    characters.  Strict length checks are applied only after canonicalization;
    service lookups also compare DB records by separator-insensitive keys so old
    stored formats remain redeemable.
    """
    cleaned = _clean_for_matching(code)
    if not cleaned:
        return None

    prefix = _normalized_prefix()
    prefix_key = claim_code_search_key(prefix)
    compact = claim_code_search_key(cleaned)
    if not compact.startswith(prefix_key):
        return None

    suffix = compact[len(prefix_key):]
    if len(suffix) != CLAIM_CODE_LENGTH or not re.fullmatch(r"[A-Z0-9]+", suffix):
        return None
    return f"{prefix}-{suffix}"


def is_plausible_claim_code(code: str) -> bool:
    """Return whether input looks like an attempted claim code.

    Used only to choose between an "invalid format" response and a "not found"
    response.  Database lookup still runs first so existing DB records are not
    rejected just because they use an older format or length.
    """
    compact = claim_code_search_key(code)
    prefix = claim_code_search_key(_normalized_prefix())
    return bool(compact and compact.startswith(prefix))


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
