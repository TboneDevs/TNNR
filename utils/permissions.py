"""Admin permission helpers."""

from config import ADMIN_IDS

_admin_cache = set(ADMIN_IDS)


def is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in _admin_cache
    except (TypeError, ValueError):
        return False


def reload_admins(admin_ids=None):
    global _admin_cache
    _admin_cache = set(admin_ids if admin_ids is not None else ADMIN_IDS)
    return _admin_cache
