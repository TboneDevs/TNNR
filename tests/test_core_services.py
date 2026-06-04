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


def test_application_builds_and_handlers_register(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    # Reload main after environment-backed modules are loaded so BOT_TOKEN/ADMIN_IDS
    # are read from the test environment, then verify PTB v20 application build and
    # handler registration do not touch private Updater internals or the network.
    sys.modules.pop("main", None)
    import main

    app = main.build_application()

    assert app.bot.token == "123:test"
    assert app.handlers
    assert app.post_init is main.validate_telegram_access
    registered_handler_count = sum(len(group) for group in app.handlers.values())
    assert registered_handler_count >= 10
    assert db.validate_startup()


def test_startup_recovery_succeeds(tmp_path, monkeypatch):
    load_app(tmp_path, monkeypatch)
    from utils.recovery_manager import recovery_manager

    assert recovery_manager.startup_recovery() is True


def _make_update(text, chat_type="private", chat_id=111, user_id=1):
    import types

    class Message:
        def __init__(self):
            self.text = text
            self.chat_id = chat_id
            self.message_id = 44
            self.replies = []

        async def reply_text(self, value):
            self.replies.append(value)

    message = Message()
    user = types.SimpleNamespace(id=user_id, username="admin", full_name="Admin User", is_bot=False)
    chat = types.SimpleNamespace(id=chat_id, type=chat_type)
    return types.SimpleNamespace(message=message, effective_user=user, effective_chat=chat), message


class _FakeChat:
    id = -1003846885691
    title = "TNNR CPM"


class _FakeMember:
    status = "administrator"
    can_post_messages = True


class _FakeBot:
    id = 999

    def __init__(self, fail=None):
        self.fail = fail
        self.sent = []

    async def get_chat(self, chat_id):
        if self.fail:
            raise self.fail
        return _FakeChat()

    async def get_chat_member(self, chat_id, user_id):
        return _FakeMember()

    async def send_message(self, chat_id, text):
        if self.fail:
            raise self.fail
        import types
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=777)


def test_trivia_create_posts_to_announcement_channel_and_stores_message_id(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import trivia_create

    update, message = _make_update("/trivia_create What car?|gtr|1 Account")
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    asyncio.run(trivia_create(update, context))

    assert context.bot.sent[0][0] == -1003846885691
    assert "Giveaway ID:" in context.bot.sent[0][1]
    row = db.execute_one("SELECT giveaway_id, status, announcement_channel_id, announcement_message_id FROM giveaways")
    assert row[1] == "active"
    assert row[2] == -1003846885691
    assert row[3] == 777
    assert "✅ Giveaway Created Successfully" in message.replies[-1]
    assert "@TnnrCPM" in message.replies[-1]
    assert "777" in message.replies[-1]


def test_giveaway_create_failure_does_not_insert_row(tmp_path, monkeypatch):
    import asyncio
    import types
    from telegram.error import BadRequest

    db = load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import guess_create

    update, message = _make_update("/guess_create 1 10 5 1 Account")
    context = types.SimpleNamespace(args=["1", "10", "5", "1", "Account"], bot=_FakeBot(BadRequest("Chat not found")))

    asyncio.run(guess_create(update, context))

    assert "CHANNEL_NOT_FOUND" in message.replies[-1]
    assert db.execute_one("SELECT COUNT(*) FROM giveaways")[0] == 0


def test_giveaway_create_blocks_public_locations(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import BLOCKED_LOCATION_MESSAGE, guess_create

    update, message = _make_update("/guess_create 1 10 5 1 Account", chat_type="supergroup", chat_id=-1001)
    context = types.SimpleNamespace(args=["1", "10", "5", "1", "Account"], bot=_FakeBot())

    asyncio.run(guess_create(update, context))

    assert message.replies[-1] == BLOCKED_LOCATION_MESSAGE
    assert db.execute_one("SELECT COUNT(*) FROM giveaways")[0] == 0
    assert context.bot.sent == []


def test_channeltest_posts_configured_test_message(tmp_path, monkeypatch):
    import asyncio
    import types

    load_app(tmp_path, monkeypatch)
    from handlers.admin_handlers import channeltest

    update, message = _make_update("/channeltest")
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)

    asyncio.run(channeltest(update, context))

    assert bot.sent == [(-1003846885691, "✅ Channel Test Successful")]
    assert "✅ Channel test passed." in message.replies[-1]
    assert "Channel ID:" in message.replies[-1]


def test_discussiontest_sends_probe_and_sets_live_read_test(tmp_path, monkeypatch):
    import asyncio
    import types

    load_app(tmp_path, monkeypatch)
    from handlers.admin_handlers import discussiontest
    from utils.channel_utils import get_discussion_read_targets

    update, message = _make_update("/discussiontest")
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)

    asyncio.run(discussiontest(update, context))

    assert (-1003994249946, "✅ Discussion Group Test Successful") in bot.sent
    assert "✅ Discussion group test started." in message.replies[-1]
    assert "PENDING LIVE TEST" in message.replies[-1]
    assert get_discussion_read_targets()[1] == 111


def test_discussion_live_read_test_notifies_admin_without_group_reply(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import collect_discussion_entry
    from utils.channel_utils import start_discussion_read_test

    start_discussion_read_test(1, 111)
    update, message = _make_update("test trivia access", chat_type="supergroup", chat_id=-1003994249946, user_id=20)
    update.effective_user.username = "reader"
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)

    asyncio.run(collect_discussion_entry(update, context))

    assert bot.sent and bot.sent[-1][0] == 111
    assert "✅ Live discussion read test passed." in bot.sent[-1][1]
    assert message.replies == []
    assert db.execute_one("SELECT COUNT(*) FROM entries")[0] == 0


def test_discussion_trivia_entries_are_normalized_silent_and_store_metadata(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import collect_discussion_entry
    from services.trivia_service import trivia_service

    gid = trivia_service.create_giveaway(
        "Car?", "Nissan   GTR", "1 Account", 1, "admin",
        -1003846885691, 555, "TRIVIA-META", "active", -1003994249946,
    )
    update, message = _make_update("  nIsSaN    gTr  ", chat_type="supergroup", chat_id=-1003994249946, user_id=21)
    update.effective_user.username = "driver"
    update.effective_user.first_name = "First"
    update.effective_user.last_name = "Last"
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)

    asyncio.run(collect_discussion_entry(update, context))

    row = db.execute_one("SELECT giveaway_id, telegram_id, username, first_name, last_name, message_id, submitted_answer, source_type FROM entries WHERE giveaway_id = ?", (gid,))
    assert row[0] == gid
    assert row[1] == 21
    assert row[2] == "driver"
    assert row[3] == "First"
    assert row[4] == "Last"
    assert row[5] == 44
    assert row[6] == "  nIsSaN    gTr  "
    assert row[7] == "discussion_group"
    assert message.replies == []
    assert bot.sent == []

    asyncio.run(collect_discussion_entry(update, context))
    assert db.execute_one("SELECT COUNT(*) FROM entries WHERE giveaway_id = ?", (gid,))[0] == 1


def test_discussion_guess_entries_are_silent_and_store_number(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import collect_discussion_entry
    from services.guess_service import guess_service

    gid = guess_service.create_giveaway(
        1, 10, 7, "1 Account", 1, "admin",
        -1003846885691, 556, "GUESS-META", "active", -1003994249946,
    )
    update, message = _make_update("7", chat_type="supergroup", chat_id=-1003994249946, user_id=22)
    update.effective_user.username = "guesser"
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)

    asyncio.run(collect_discussion_entry(update, context))

    row = db.execute_one("SELECT giveaway_id, telegram_id, guessed_number, source_type FROM entries WHERE giveaway_id = ?", (gid,))
    assert row[0] == gid
    assert row[1] == 22
    assert row[2] == 7
    assert row[3] == "discussion_group"
    assert message.replies == []
    assert bot.sent == []
