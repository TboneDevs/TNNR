import importlib
import os
import sys


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("ADMIN_IDS", "1,2")
    monkeypatch.setenv("CLAIM_CODE_PREFIX", "CPM")
    monkeypatch.setenv("CLAIM_CODE_LENGTH", "6")
    for name in list(sys.modules):
        if name == "config" or name.startswith(("database", "services", "utils", "handlers")):
            sys.modules.pop(name, None)
    import config  # noqa: F401
    from database.database import db
    db.initialize()
    return db


def test_database_initializes_required_tables(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    assert db.validate_startup()
    tables = {row[0] for row in db.execute_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"giveaways", "entries", "winners", "account_pool", "audit_logs", "schema_migrations"} <= tables


def test_validators_accept_and_reject_inputs(tmp_path, monkeypatch):
    load_app(tmp_path, monkeypatch)
    from utils.validators import normalize_text, validate_account_format, validate_number
    assert normalize_text("  Nissan   GTR ") == "nissan gtr"
    assert validate_number("47", 1, 100) == (True, 47)
    assert validate_number("47.2", 1, 100) == (False, None)
    assert validate_account_format("User@Example.com:secret") == (True, "user@example.com", "secret")
    assert validate_account_format("bad-line")[0] is False


def test_pool_import_and_claim_redemption(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.claim_service import claim_service
    from services.pool_service import pool_service

    result = pool_service.import_accounts(["a@example.com:p1", "b@example.com:p2", "bad"], 1, "admin")
    assert result["added"] == 2
    db.execute(
        """INSERT INTO giveaways (giveaway_id, type, prize, status, created_by)
           VALUES (?, ?, ?, ?, ?)""",
        ("TRIVIA-TEST", "trivia", "2 Accounts", "winner_selected", 1),
    )
    db.execute(
        """INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize)
           VALUES (?, ?, ?, ?, ?)""",
        ("CPM-ABC123", "TRIVIA-TEST", 10, "winner", "2 Accounts"),
    )
    db.commit()

    redeemed = claim_service.redeem_claim_code("CPM-ABC123", 10, "winner")
    assert redeemed["success"] is True
    assert len(redeemed["accounts"]) == 2
    assert claim_service.redeem_claim_code("CPM-ABC123", 10, "winner")["success"] is False


def test_guess_winner_selection(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.guess_service import guess_service

    gid = guess_service.create_giveaway(1, 100, 50, "1 Account", 1, "admin")
    assert gid
    assert guess_service.submit_entry(gid, 10, "u10", "User 10", 101, "49")
    assert guess_service.submit_entry(gid, 11, "u11", "User 11", 102, "70")
    result = guess_service.select_winner(gid, 1, "admin")
    assert result["winner_telegram_id"] == 10
    assert result["claim_code"].startswith("CPM-")
