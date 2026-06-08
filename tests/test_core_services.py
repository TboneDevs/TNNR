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
    assert result["owed_amount"] == 1
    assert result["allocation_success"] is True
    assert db.execute_one("SELECT owed_amount FROM account_entitlements WHERE telegram_id = ?", (10,))[0] == 1


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
    assert app.post_init is main.startup_post_init
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


def test_winner_announcement_includes_mycodes_instructions(tmp_path, monkeypatch):
    import asyncio
    import types

    monkeypatch.setenv("ADMIN_LOG_CHANNEL_ID", "-1005555555555")
    load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import _announce_winner

    result = {
        "winner_telegram_id": 42,
        "winner_username": "winneruser",
        "display_name": "Winner User",
        "first_name": "Winner",
        "last_name": "User",
        "source_message_id": 1234,
        "source_type": "discussion_group",
        "owed_amount": 5,
        "allocation_success": True,
        "allocation_message": None,
        "prize": "5 Accounts",
        "giveaway_id": "TRIVIA-PRIVATE",
        "giveaway_type": "trivia",
    }
    update, message = _make_update("/trivia_draw TRIVIA-PRIVATE")
    bot = _FakeBot()
    context = types.SimpleNamespace(args=["TRIVIA-PRIVATE"], bot=bot)

    asyncio.run(_announce_winner(update, context, result))

    public_message = bot.sent[0]
    winner_dm = bot.sent[1]
    admin_log = bot.sent[2]

    assert public_message[0] == -1003846885691
    assert "CPM-SECRET1" not in public_message[1]
    assert "@AccountTool_Bot" in public_message[1]
    assert "/claim" in public_message[1]
    assert "pending balance" in public_message[1]
    assert "@winneruser" in public_message[1]
    assert "42" in public_message[1]

    assert winner_dm[0] == 42
    assert "CPM-SECRET1" not in winner_dm[1]
    assert "/claim" in winner_dm[1]
    assert "pending balance" in winner_dm[1]

    assert admin_log[0] == -1005555555555
    assert "Owed amount allocated: 5" in admin_log[1]
    assert "Username: @winneruser" in admin_log[1]
    assert "Telegram ID: 42" in admin_log[1]
    assert "Giveaway ID: TRIVIA-PRIVATE" in admin_log[1]
    assert "Public announcement sent: yes" in admin_log[1]
    assert "Winner DM sent: yes" in admin_log[1]
    assert message.replies[-1] == "✅ Winner selected and announced."


def test_spin_win_includes_claim_code_and_mycodes_instructions(tmp_path, monkeypatch):
    import asyncio
    import types

    monkeypatch.setenv("ADMIN_LOG_CHANNEL_ID", "-1005555555555")
    db = load_app(tmp_path, monkeypatch)
    from handlers.giveaway_handlers import collect_discussion_entry
    from services.lottery_service import lottery_service

    gid = lottery_service.create_giveaway(
        "5 Accounts", 1.0, 1, "admin",
        -1003846885691, 888, "SPIN-PRIVATE", "active", -1003994249946,
    )
    update, message = _make_update("spin", chat_type="supergroup", chat_id=-1003994249946, user_id=43)
    update.effective_user.username = "spinwinner"
    update.effective_user.first_name = "Spin"
    update.effective_user.last_name = "Winner"
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)

    asyncio.run(collect_discussion_entry(update, context))

    winner = db.execute_one("SELECT claim_code FROM winners WHERE giveaway_id = ?", (gid,))
    assert winner and winner[0].startswith("DIRECT-")
    owed = db.execute_one("SELECT owed_amount FROM account_entitlements WHERE telegram_id = ?", (43,))
    assert owed and owed[0] == 5
    claim_code = winner[0]
    assert message.replies == []
    assert len(bot.sent) == 3

    public_message = bot.sent[0]
    winner_dm = bot.sent[1]
    admin_log = bot.sent[2]

    assert public_message[0] == -1003846885691
    assert claim_code not in public_message[1]
    assert "@AccountTool_Bot" in public_message[1]
    assert "/claim" in public_message[1]
    assert "@spinwinner" in public_message[1]
    assert "43" in public_message[1]

    assert winner_dm[0] == 43
    assert claim_code not in winner_dm[1]
    assert "/claim" in winner_dm[1]

    assert admin_log[0] == -1005555555555
    assert "Owed amount allocated: 5" in admin_log[1]
    assert "Username: @spinwinner" in admin_log[1]


def test_claimcode_redeem_is_blocked_outside_private_dm(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import claimcode

    db.execute(
        """INSERT INTO giveaways (giveaway_id, type, prize, status, created_by)
           VALUES (?, ?, ?, ?, ?)""",
        ("TRIVIA-PUBLIC-BLOCK", "trivia", "1 Account", "winner_selected", 1),
    )
    db.execute(
        """INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize)
           VALUES (?, ?, ?, ?, ?)""",
        ("CPM-PRIVATE", "TRIVIA-PUBLIC-BLOCK", 99, "winner", "1 Account"),
    )
    db.commit()

    update, message = _make_update("/claimcode CPM-PRIVATE", chat_type="supergroup", chat_id=-1003994249946, user_id=99)
    context = types.SimpleNamespace(args=["CPM-PRIVATE"], bot=_FakeBot())

    asyncio.run(claimcode(update, context))

    assert message.replies[-1] == "For privacy, account details can only be viewed in DMs. Please message the bot directly."
    assert "CPM-PRIVATE" not in message.replies[-1]
    assert db.execute_one("SELECT claimed_status FROM winners WHERE claim_code = ?", ("CPM-PRIVATE",))[0] == 0


def test_claim_code_normalization_accepts_safe_variations(tmp_path, monkeypatch):
    load_app(tmp_path, monkeypatch)
    from utils.claimcode import normalize_claim_code, validate_claim_code_format

    assert normalize_claim_code("CPM-ABC123") == "CPM-ABC123"
    assert normalize_claim_code(" cpm-abc123 ") == "CPM-ABC123"
    assert normalize_claim_code("cPm - aBc123") == "CPM-ABC123"
    assert normalize_claim_code("CPM ABC123") == "CPM-ABC123"
    assert normalize_claim_code("CPMABC123") == "CPM-ABC123"
    assert normalize_claim_code("cpmabc123") == "CPM-ABC123"
    assert normalize_claim_code("CPM_ABC123") == "CPM-ABC123"
    assert normalize_claim_code("CPM--ABC123") == "CPM-ABC123"
    assert validate_claim_code_format(" cpm - abc123 ") is True
    assert normalize_claim_code("CPM-ABC12") is None
    assert normalize_claim_code("BAD-ABC123") is None


def test_claim_redemption_normalizes_lookup_and_preserves_stored_code(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.claim_service import claim_service
    from services.pool_service import pool_service

    pool_service.import_accounts(["norm@example.com:p1"], 1, "admin")
    db.execute(
        """INSERT INTO giveaways (giveaway_id, type, prize, status, created_by)
           VALUES (?, ?, ?, ?, ?)""",
        ("TRIVIA-NORM", "trivia", "1 Account", "winner_selected", 1),
    )
    db.execute(
        """INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize)
           VALUES (?, ?, ?, ?, ?)""",
        ("CPM-AbC123", "TRIVIA-NORM", 10, "winner", "1 Account"),
    )
    db.commit()

    redeemed = claim_service.redeem_claim_code("  cpm - abc123  ", 10, "winner")

    assert redeemed["success"] is True
    assert redeemed["accounts"] == ["norm@example.com:p1"]
    assert db.execute_one("SELECT claimed_status FROM winners WHERE claim_code = ?", ("CPM-AbC123",))[0] == 1
    assert db.execute_one("SELECT claim_code FROM redemptions")[0] == "CPM-AbC123"
    assert db.execute_one("SELECT assigned_claim_code FROM account_pool WHERE email = ?", ("norm@example.com",))[0] == "CPM-AbC123"


def test_claim_redemption_reports_already_redeemed_after_normalized_lookup(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.claim_service import claim_service

    db.execute(
        """INSERT INTO giveaways (giveaway_id, type, prize, status, created_by)
           VALUES (?, ?, ?, ?, ?)""",
        ("TRIVIA-USED", "trivia", "1 Account", "winner_selected", 1),
    )
    db.execute(
        """INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize, claimed_status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("CPM-AbC124", "TRIVIA-USED", 10, "winner", "1 Account", 1),
    )
    db.commit()

    result = claim_service.redeem_claim_code("cpm-abc124", 10, "winner")

    assert result["success"] is False
    assert result["message"] == "⚠️ This claim code has already been redeemed."


def test_claimcode_handler_joins_spaced_code_args_in_private_dm(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import claimcode
    from services.pool_service import pool_service

    pool_service.import_accounts(["handler@example.com:p1"], 1, "admin")
    db.execute(
        """INSERT INTO giveaways (giveaway_id, type, prize, status, created_by)
           VALUES (?, ?, ?, ?, ?)""",
        ("TRIVIA-HANDLER", "trivia", "1 Account", "winner_selected", 1),
    )
    db.execute(
        """INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize)
           VALUES (?, ?, ?, ?, ?)""",
        ("CPM-AbC125", "TRIVIA-HANDLER", 55, "winner", "1 Account"),
    )
    db.commit()

    update, message = _make_update("/claimcode cpm - abc125", chat_type="private", user_id=55)
    update.effective_user.username = "winner"
    context = types.SimpleNamespace(args=["cpm", "-", "abc125"], bot=_FakeBot())

    asyncio.run(claimcode(update, context))

    assert "✅ Prize Delivered Successfully" in message.replies[-1]
    assert "handler@example.com:p1" in message.replies[-1]
    assert db.execute_one("SELECT claimed_status FROM winners WHERE claim_code = ?", ("CPM-AbC125",))[0] == 1


def test_winner_generated_direct_balance_is_deliverable(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.guess_service import guess_service
    from services.direct_delivery_service import direct_delivery_service
    from services.pool_service import pool_service

    pool_service.import_accounts(["draw@example.com:p1"], 1, "admin")
    gid = guess_service.create_giveaway(1, 10, 7, "1 Account", 1, "admin")
    assert guess_service.submit_entry(gid, 77, "drawinner", "Draw Winner", 7001, "7")

    result = guess_service.select_winner(gid, 1, "admin")
    assert result["owed_amount"] == 1
    assert result["allocation_success"] is True
    assert db.execute_one("SELECT owed_amount FROM account_entitlements WHERE telegram_id = ?", (77,))[0] == 1
    delivered = direct_delivery_service.attempt_delivery_for_user(77, "drawinner", "test")
    assert delivered["success"] is True
    assert delivered["accounts"] == ["draw@example.com:p1"]
    assert direct_delivery_service.get_pending_amount(77) == 0


def test_mycodes_no_codes_response(tmp_path, monkeypatch):
    import asyncio
    import types

    load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import mycodes

    update, message = _make_update("/mycodes", chat_type="private", user_id=200)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    asyncio.run(mycodes(update, context))

    assert "🎟️ My Pending Accounts" in message.replies[-1]
    assert "You do not currently have any unclaimed account credits." in message.replies[-1]


def test_mycodes_lists_pending_direct_balance_without_credentials(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import mycodes
    from services.direct_delivery_service import direct_delivery_service

    direct_delivery_service.allocate_owed_accounts(200, 2, "test", prize="2 Accounts")
    direct_delivery_service.allocate_owed_accounts(201, 9, "test", prize="9 Accounts")

    update, message = _make_update("/mycodes", chat_type="private", user_id=200)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    asyncio.run(mycodes(update, context))

    text = message.replies[-1]
    assert "🎟️ My Pending Accounts" in text
    assert "You have 2 unclaimed account credit(s)." in text
    assert "email" not in text.lower()
    assert "password" not in text.lower()
    assert "9 pending" not in text


def test_claim_lookup_accepts_old_no_hyphen_stored_codes(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.claim_service import claim_service
    from services.pool_service import pool_service

    pool_service.import_accounts(["old@example.com:p1"], 1, "admin")
    db.execute("INSERT INTO giveaways (giveaway_id, type, prize, status, created_by) VALUES (?, ?, ?, ?, ?)", ("OLD-CODE", "trivia", "1 Account", "winner_selected", 1))
    db.execute("INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize) VALUES (?, ?, ?, ?, ?)", ("CPMABC123", "OLD-CODE", 300, "olduser", "1 Account"))
    db.commit()

    result = claim_service.redeem_claim_code("cpm-abc123", 300, "olduser")

    assert result["success"] is True
    assert result["claim_code"] == "CPMABC123"
    assert result["accounts"] == ["old@example.com:p1"]


def test_claim_redemption_rejects_other_user_and_insufficient_inventory(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.claim_service import claim_service

    db.execute("INSERT INTO giveaways (giveaway_id, type, prize, status, created_by) VALUES (?, ?, ?, ?, ?)", ("OWN-CODE", "trivia", "2 Accounts", "winner_selected", 1))
    db.execute("INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize) VALUES (?, ?, ?, ?, ?)", ("CPM-OWN123", "OWN-CODE", 301, "owner", "2 Accounts"))
    db.commit()

    other = claim_service.redeem_claim_code("CPM-OWN123", 302, "other")
    assert other["success"] is False
    assert "belongs to another Telegram account" in other["message"]

    insufficient = claim_service.redeem_claim_code("CPM-OWN123", 301, "owner")
    assert insufficient["success"] is False
    assert insufficient["message"] == "Not enough accounts available in inventory"


def test_claimcode_handler_accepts_underscore_and_sends_admin_log(tmp_path, monkeypatch):
    import asyncio
    import types

    monkeypatch.setenv("ADMIN_LOG_CHANNEL_ID", "-1005555555555")
    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import claimcode
    from services.pool_service import pool_service

    pool_service.import_accounts(["underscore@example.com:p1"], 1, "admin")
    db.execute("INSERT INTO giveaways (giveaway_id, type, prize, status, created_by) VALUES (?, ?, ?, ?, ?)", ("UNDER-CODE", "trivia", "1 Account", "winner_selected", 1))
    db.execute("INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize) VALUES (?, ?, ?, ?, ?)", ("CPM-ABC123", "UNDER-CODE", 400, "under", "1 Account"))
    db.commit()

    update, message = _make_update("/claimcode CPM_ABC123", chat_type="private", user_id=400)
    update.effective_user.username = "under"
    context = types.SimpleNamespace(args=["CPM_ABC123"], bot=_FakeBot())

    asyncio.run(claimcode(update, context))

    assert "✅ Prize Delivered Successfully" in message.replies[-1]
    assert "underscore@example.com:p1" in message.replies[-1]
    assert any(sent[0] == -1005555555555 and "Claim code: CPM-ABC123" in sent[1] for sent in context.bot.sent)


def test_start_and_help_include_mycodes_guidance(tmp_path, monkeypatch):
    import asyncio
    import types

    load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import help_command, start

    update, message = _make_update("/start", chat_type="private", user_id=500)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(start(update, context))
    assert "/claim" in message.replies[0]
    assert "no unclaimed accounts" in message.replies[0]

    update, message = _make_update("/help", chat_type="private", user_id=500)
    asyncio.run(help_command(update, context))
    assert "User Commands:" in message.replies[-1]
    assert "/claim" in message.replies[-1]
    assert "/give TELEGRAM_ID AMOUNT" in message.replies[-1]


def test_claim_code_normalization_handles_unicode_and_hidden_copy_paste(tmp_path, monkeypatch):
    load_app(tmp_path, monkeypatch)
    from utils.claimcode import claim_code_search_key, normalize_claim_code

    assert normalize_claim_code("CPM–ABC123") == "CPM-ABC123"
    assert normalize_claim_code("CPM—ABC123") == "CPM-ABC123"
    assert normalize_claim_code("\u200bCPM\u00a0-\u00a0ABC123\ufeff") == "CPM-ABC123"
    assert normalize_claim_code("CPM\nABC123") == "CPM-ABC123"
    assert claim_code_search_key("CPM—ABC123") == "CPMABC123"


def test_claim_lookup_finds_code_registered_only_in_claim_codes_table(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.claim_service import claim_service
    from services.pool_service import pool_service

    pool_service.import_accounts(["claimcodes@example.com:p1"], 1, "admin")
    db.execute("INSERT INTO giveaways (giveaway_id, type, prize, status, created_by) VALUES (?, ?, ?, ?, ?)", ("CLAIM-CODES-TABLE", "trivia", "1 Account", "winner_selected", 1))
    cur = db.execute("INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize) VALUES (?, ?, ?, ?, ?)", ("LEGACYWIN1", "CLAIM-CODES-TABLE", 901, "winner", "1 Account"))
    winner_id = cur.lastrowid
    db.execute("INSERT INTO claim_codes (code, winner_id, telegram_id, prize, status) VALUES (?, ?, ?, ?, ?)", ("CPM-ABC123", winner_id, 901, "1 Account", "unclaimed"))
    db.commit()

    result = claim_service.redeem_claim_code("cpm—abc123", 901, "winner")

    assert result["success"] is True
    assert result["claim_code"] == "LEGACYWIN1"
    assert result["accounts"] == ["claimcodes@example.com:p1"]


def test_claimcode_handler_extracts_raw_text_with_bot_suffix_and_newline(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import claimcode
    from services.pool_service import pool_service

    pool_service.import_accounts(["rawtext@example.com:p1"], 1, "admin")
    db.execute("INSERT INTO giveaways (giveaway_id, type, prize, status, created_by) VALUES (?, ?, ?, ?, ?)", ("RAW-TEXT", "trivia", "1 Account", "winner_selected", 1))
    db.execute("INSERT INTO winners (claim_code, giveaway_id, telegram_id, username, prize) VALUES (?, ?, ?, ?, ?)", ("CPM-ABC123", "RAW-TEXT", 902, "winner", "1 Account"))
    db.commit()

    update, message = _make_update("/claimcode@AccountTool_Bot\nCPM\u00a0—\u00a0ABC123", chat_type="private", user_id=902)
    update.effective_user.username = "winner"
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    asyncio.run(claimcode(update, context))

    assert "✅ Prize Delivered Successfully" in message.replies[-1]
    assert "rawtext@example.com:p1" in message.replies[-1]


def test_claim_validation_distinguishes_not_found_from_malformed(tmp_path, monkeypatch):
    load_app(tmp_path, monkeypatch)
    from services.claim_service import claim_service

    plausible = claim_service.redeem_claim_code("CPM-NOT999", 903, "user")
    malformed = claim_service.redeem_claim_code("not a cpm code", 903, "user")

    assert plausible["success"] is False
    assert plausible["message"].startswith("❌ Claim code not found.")
    assert malformed["success"] is False
    assert malformed["message"].startswith("❌ Invalid claim code.")


def test_mycodes_legacy_command_does_not_show_claim_codes(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import mycodes
    from services.direct_delivery_service import direct_delivery_service

    direct_delivery_service.allocate_owed_accounts(904, 1, "test", prize="1 Account")
    update, message = _make_update("/mycodes", chat_type="private", user_id=904)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    asyncio.run(mycodes(update, context))

    assert "1 unclaimed account credit" in message.replies[-1]
    assert "/claim" in message.replies[-1]


def test_bonus_success_awards_credit_sets_cooldown_and_logs(tmp_path, monkeypatch):
    import asyncio
    import types

    monkeypatch.setenv("ADMIN_LOG_CHANNEL_ID", "-1005555555555")
    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import bonus

    update, message = _make_update("/bonus", chat_type="supergroup", chat_id=-444, user_id=1001)
    update.effective_user.username = "bonususer"
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    asyncio.run(bonus(update, context))

    assert "✅ Bonus credit added." in message.replies[-1]
    assert db.execute_one("SELECT COUNT(*) FROM bonus_claims WHERE telegram_id = ? AND status = 'delivered'", (1001,))[0] == 1
    assert db.execute_one("SELECT COALESCE(SUM(owed_amount - delivered_amount), 0) FROM account_entitlements WHERE telegram_id = ?", (1001,))[0] == 1
    assert any(sent[0] == -1005555555555 and "Bonus credit claimed" in sent[1] and "New balance: 1" in sent[1] for sent in context.bot.sent)


def test_bonus_cooldown_blocks_repeat_and_persists(tmp_path, monkeypatch):
    import asyncio
    import types
    import importlib

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import bonus
    from services.pool_service import pool_service

    pool_service.import_accounts(["cool1@example.com:p1", "cool2@example.com:p2"], 1, "admin")
    update, message = _make_update("/bonus", chat_type="private", user_id=1002)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(bonus(update, context))

    # Simulate a restart/re-open by closing and re-opening the shared DB.
    db.close()
    db.connect()
    update2, message2 = _make_update("/bonus", chat_type="private", user_id=1002)
    context2 = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(bonus(update2, context2))

    assert "You already claimed a bonus credit" in message2.replies[-1]
    assert "day" in message2.replies[-1] or "hour" in message2.replies[-1]
    assert db.execute_one("SELECT COALESCE(SUM(owed_amount - delivered_amount), 0) FROM account_entitlements WHERE telegram_id = ?", (1002,))[0] == 1
    assert db.execute_one("SELECT COUNT(*) FROM bonus_claims WHERE telegram_id = ? AND status = 'delivered'", (1002,))[0] == 1


class _SelectiveFailBot(_FakeBot):
    def __init__(self, fail_chat_id=None):
        super().__init__()
        self.fail_chat_id = fail_chat_id

    async def send_message(self, chat_id, text):
        if chat_id == self.fail_chat_id:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        return await super().send_message(chat_id, text)


def test_bonus_awards_credit_without_touching_stock(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import bonus
    from services.pool_service import pool_service

    pool_service.import_accounts(["dmfail@example.com:p1"], 1, "admin")
    update, message = _make_update("/bonus", chat_type="supergroup", chat_id=-444, user_id=1003)
    context = types.SimpleNamespace(args=[], bot=_SelectiveFailBot(fail_chat_id=1003))

    asyncio.run(bonus(update, context))

    assert "Bonus credit added" in message.replies[-1]
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'available'")[0] == 1
    assert db.execute_one("SELECT COUNT(*) FROM bonus_claims WHERE telegram_id = ? AND status = 'delivered'", (1003,))[0] == 1
    assert db.execute_one("SELECT COALESCE(SUM(owed_amount - delivered_amount), 0) FROM account_entitlements WHERE telegram_id = ?", (1003,))[0] == 1


def test_bonus_does_not_require_stock_for_credit_award(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import bonus

    update, message = _make_update("/bonus", chat_type="private", user_id=1004)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    asyncio.run(bonus(update, context))

    assert "Bonus credit added" in message.replies[-1]
    assert db.execute_one("SELECT COUNT(*) FROM bonus_claims WHERE telegram_id = ? AND status = 'delivered'", (1004,))[0] == 1
    assert db.execute_one("SELECT COALESCE(SUM(owed_amount - delivered_amount), 0) FROM account_entitlements WHERE telegram_id = ?", (1004,))[0] == 1


def test_bonus_duplicate_cooldown_does_not_double_award(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import bonus

    update, message = _make_update("/bonus", chat_type="private", user_id=1005)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(bonus(update, context))
    update2, message2 = _make_update("/bonus", chat_type="private", user_id=1005)
    context2 = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(bonus(update2, context2))

    assert "already claimed a bonus credit" in message2.replies[-1]
    assert db.execute_one("SELECT COALESCE(SUM(owed_amount - delivered_amount), 0) FROM account_entitlements WHERE telegram_id = ?", (1005,))[0] == 1


def test_bonus_handler_registered_and_help_mentions_bonus(tmp_path, monkeypatch):
    import asyncio
    import types

    load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import help_command
    import main

    app = main.build_application()
    command_names = set()
    for group in app.handlers.values():
        for handler in group:
            commands = getattr(handler, "commands", None)
            if commands:
                command_names.update(commands)
    assert "bonus" in command_names

    update, message = _make_update("/help", chat_type="private", user_id=1006)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(help_command(update, context))
    assert "/bonus" in message.replies[-1]
    assert "5 days" in message.replies[-1]


def test_start_and_private_dm_do_not_auto_deliver_accounts(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import private_delivery_check, start
    from services.direct_delivery_service import direct_delivery_service
    from services.pool_service import pool_service

    direct_delivery_service.allocate_owed_accounts(1200, 1, "test", prize="1 Account")
    pool_service.import_accounts(["auto@example.com:p1"], 1, "admin")
    context = types.SimpleNamespace(args=[], bot=_FakeBot())

    update, message = _make_update("/start", chat_type="private", user_id=1200)
    asyncio.run(start(update, context))
    assert "/claim" in message.replies[-1]
    assert direct_delivery_service.get_pending_amount(1200) == 1
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'available'")[0] == 1

    update2, message2 = _make_update("hello", chat_type="private", user_id=1200)
    asyncio.run(private_delivery_check(update2, context))
    assert "Run /claim" in message2.replies[-1]
    assert direct_delivery_service.get_pending_amount(1200) == 1
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'available'")[0] == 1


def test_balance_bet_slots_and_coinflip_credit_safety(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import balance, bet, coinflip, slots
    from services.direct_delivery_service import OUT_OF_CREDITS_MESSAGE, direct_delivery_service

    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    update, message = _make_update("/balance", chat_type="private", user_id=1300)
    asyncio.run(balance(update, context))
    assert "Promotional Credits (non-withdrawable): 0" in message.replies[-1]
    assert "Withdrawable Credits: 0" in message.replies[-1]

    update, message = _make_update("/bet 0", chat_type="private", user_id=1300)
    context.args = ["0"]
    asyncio.run(bet(update, context))
    assert message.replies[-1] == "Usage: /bet 1"

    update, message = _make_update("/slots", chat_type="private", user_id=1300)
    context.args = []
    asyncio.run(slots(update, context))
    assert message.replies[-1] == OUT_OF_CREDITS_MESSAGE

    direct_delivery_service.allocate_owed_accounts(1300, 2, "test", prize="2 Accounts")
    update, message = _make_update("/bet 2", chat_type="private", user_id=1300)
    context.args = ["2"]
    asyncio.run(bet(update, context))
    assert "Bet amount saved" in message.replies[-1]

    # Exercise deterministic service-level odds: lose then big 6-credit win.
    lost = direct_delivery_service.play_slots(1300, "player", roll=0.49)
    assert lost["won"] == 0 and lost["balance"] == 1 and lost["promotional_balance"] == 0
    win = direct_delivery_service.play_slots(1300, "player", roll=0.95)
    assert win["won"] == 6 and win["tier"] == "big_win" and win["balance"] == 6 and win["wager_source"] == "withdrawable"

    cf_loss = direct_delivery_service.play_coinflip(1300, "heads", "player", roll=0.90)
    assert cf_loss["won"] is False and cf_loss["balance"] == 5
    cf_win = direct_delivery_service.play_coinflip(1300, "tails", "player", roll=0.10)
    assert cf_win["won"] is True and cf_win["balance"] == 6
    assert direct_delivery_service.get_pending_amount(1300) == 6
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'delivered'")[0] == 0


def test_game_odds_tables_are_50_50():
    from services.direct_delivery_service import (
        COINFLIP_WIN_PROBABILITY,
        SLOT_TIERS,
        calculate_slot_outcome,
    )

    assert COINFLIP_WIN_PROBABILITY == 0.50
    assert sum(probability for _, probability, _ in SLOT_TIERS) == 1.0
    assert SLOT_TIERS == (
        ("lose", 0.50, 0),
        ("small_win", 0.30, 1),
        ("medium_win", 0.13, 3),
        ("big_win", 0.05, 6),
        ("jackpot_win", 0.02, 80),
    )
    assert calculate_slot_outcome(0.49) == (0, "lose")
    assert calculate_slot_outcome(0.50) == (1, "small_win")
    assert calculate_slot_outcome(0.79) == (1, "small_win")
    assert calculate_slot_outcome(0.80) == (3, "medium_win")
    assert calculate_slot_outcome(0.92) == (3, "medium_win")
    assert calculate_slot_outcome(0.93) == (6, "big_win")
    assert calculate_slot_outcome(0.97) == (6, "big_win")
    assert calculate_slot_outcome(0.98) == (80, "jackpot_win")


def test_claim_withdraw_partial_and_dm_failure_safety(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import claim, withdraw
    from services.direct_delivery_service import direct_delivery_service
    from services.pool_service import pool_service

    pool_service.import_accounts(["claim1@example.com:p1", "claim2@example.com:p2", "claim3@example.com:p3"], 1, "admin")
    direct_delivery_service.allocate_owed_accounts(1400, 2, "test", prize="2 Accounts")
    update, message = _make_update("/claim", chat_type="private", user_id=1400)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(claim(update, context))
    assert any(sent[0] == 1400 and "claim1@example.com:p1" in sent[1] and "claim2@example.com:p2" in sent[1] for sent in context.bot.sent)
    assert direct_delivery_service.get_pending_amount(1400) == 0
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'delivered' AND assigned_user = ?", (1400,))[0] == 2

    # DM failure preserves reserved stock and balance.
    direct_delivery_service.allocate_owed_accounts(1401, 1, "test", prize="1 Account")
    update2, message2 = _make_update("/claim", chat_type="supergroup", chat_id=-99, user_id=1401)
    context2 = types.SimpleNamespace(args=[], bot=_SelectiveFailBot(fail_chat_id=1401))
    asyncio.run(claim(update2, context2))
    assert message2.replies[-1] == "Please start the bot in DMs first, then run the command again."
    assert direct_delivery_service.get_pending_amount(1401) == 1
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'available'")[0] == 1

    # Partial fulfillment delivers available stock and leaves the rest as credits.
    direct_delivery_service.allocate_owed_accounts(1402, 3, "test", prize="3 Accounts")
    update3, message3 = _make_update("/withdraw", chat_type="private", user_id=1402)
    context3 = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(withdraw(update3, context3))
    assert any(sent[0] == 1402 and "claim3@example.com:p3" in sent[1] for sent in context3.bot.sent)
    assert direct_delivery_service.get_pending_amount(1402) == 2
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'delivered' AND assigned_user = ?", (1402,))[0] == 1


def test_leaderboard_and_giveaway_credit_allocation_no_account_exposure(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import leaderboard
    from services.direct_delivery_service import direct_delivery_service
    from services.guess_service import guess_service

    gid = guess_service.create_giveaway(1, 10, 7, "3 Accounts", 1, "admin")
    guess_service.submit_entry(gid, 1500, "winnername", "Winner Name", 501, "7")
    result = guess_service.select_winner(gid, 1, "admin")
    assert result["owed_amount"] == 3
    assert direct_delivery_service.get_pending_amount(1500) == 3
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'delivered'")[0] == 0

    update, message = _make_update("/leaderboard", chat_type="private", user_id=1500)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(leaderboard(update, context))
    assert "Free Credit Leaderboard" in message.replies[-1]
    assert "winnername" in message.replies[-1]
    assert "@" in message.replies[-1]
    assert "email" not in message.replies[-1].lower()
    assert "password" not in message.replies[-1].lower()



def test_public_claim_invocation_never_replies_with_credentials_and_dm_failure_is_safe(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import claim
    from services.direct_delivery_service import direct_delivery_service
    from services.pool_service import pool_service
    from utils.privacy import START_DM_FIRST_MESSAGE

    pool_service.import_accounts(["publicsafe@example.com:p1"], 1, "admin")
    direct_delivery_service.allocate_owed_accounts(1600, 1, "test", prize="1 Account")
    update, message = _make_update("/claim", chat_type="supergroup", chat_id=-1003994249946, user_id=1600)
    context = types.SimpleNamespace(args=[], bot=_FakeBot(fail=RuntimeError("bot cannot dm user")))

    asyncio.run(claim(update, context))

    assert message.replies[-1] == START_DM_FIRST_MESSAGE
    assert "publicsafe@example.com" not in message.replies[-1]
    assert direct_delivery_service.get_pending_amount(1600) == 1
    assert db.execute_one("SELECT status FROM account_pool WHERE email = ?", ("publicsafe@example.com",))[0] == "available"


def test_public_claim_success_only_confirms_dm_without_credentials(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import claim
    from services.direct_delivery_service import direct_delivery_service
    from services.pool_service import pool_service

    pool_service.import_accounts(["privatedm@example.com:p1"], 1, "admin")
    direct_delivery_service.allocate_owed_accounts(1601, 1, "test", prize="1 Account")
    update, message = _make_update("/claim", chat_type="supergroup", chat_id=-1003994249946, user_id=1601)
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)

    asyncio.run(claim(update, context))

    assert message.replies[-1] == "✅ 1 account(s) sent to your DM."
    assert "privatedm@example.com" not in message.replies[-1]
    assert any(sent[0] == 1601 and "privatedm@example.com:p1" in sent[1] for sent in bot.sent)
    assert direct_delivery_service.get_pending_amount(1601) == 0


def test_credit_event_admin_and_user_claim_flow(tmp_path, monkeypatch):
    import asyncio
    import types

    monkeypatch.setenv("ADMIN_LOG_CHANNEL_ID", "-1005555555555")
    db = load_app(tmp_path, monkeypatch)
    from handlers.admin_handlers import creditevent
    from handlers.claim_handlers import eventclaim
    from services.direct_delivery_service import direct_delivery_service

    admin_update, admin_message = _make_update("/creditevent", chat_type="private", user_id=1)
    bot = _FakeBot()
    context = types.SimpleNamespace(args=[], bot=bot)
    asyncio.run(creditevent(admin_update, context))

    assert admin_message.replies[-1] == "Credit event posted successfully. Users can now claim 3 promotional credits with /eventclaim."
    assert db.execute_one("SELECT COUNT(*) FROM credit_events WHERE status = 'active'")[0] == 1
    assert any(sent[0] == -1003846885691 and "/eventclaim" in sent[1] for sent in bot.sent)

    user_update, user_message = _make_update("/eventclaim", chat_type="private", user_id=1700)
    user_update.effective_user.username = "eventuser"
    asyncio.run(eventclaim(user_update, context))
    assert user_message.replies[-1] == "Success! You received 3 event credits."
    assert direct_delivery_service.get_pending_amount(1700) == 0
    assert direct_delivery_service.get_promotional_amount(1700) == 3

    dup_update, dup_message = _make_update("/eventclaim", chat_type="private", user_id=1700)
    asyncio.run(eventclaim(dup_update, context))
    assert dup_message.replies[-1] == "You have already claimed this event credit top-up."
    assert direct_delivery_service.get_pending_amount(1700) == 0
    assert direct_delivery_service.get_promotional_amount(1700) == 3

    admin_update2, admin_message2 = _make_update("/creditevent", chat_type="private", user_id=1)
    asyncio.run(creditevent(admin_update2, context))
    user_update2, user_message2 = _make_update("/eventclaim", chat_type="private", user_id=1700)
    asyncio.run(eventclaim(user_update2, context))
    assert user_message2.replies[-1] == "Success! You received 3 event credits."
    assert direct_delivery_service.get_pending_amount(1700) == 0
    assert direct_delivery_service.get_promotional_amount(1700) == 6



def test_promotional_event_credits_cannot_be_withdrawn_and_convert_only_winnings(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import balance, claim, withdraw
    from services.credit_event_service import credit_event_service
    from services.direct_delivery_service import PROMOTIONAL_WITHDRAW_MESSAGE, direct_delivery_service
    from services.pool_service import pool_service

    credit_event_service.create_event(1, "admin", -1003846885691, 555)
    result = credit_event_service.claim_current_event(1900, "promo")
    assert result["success"] is True
    assert direct_delivery_service.get_promotional_amount(1900) == 3
    assert direct_delivery_service.get_pending_amount(1900) == 0

    pool_service.import_accounts(["promo1@example.com:p1"], 1, "admin")
    update, message = _make_update("/claim", chat_type="private", user_id=1900)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(claim(update, context))
    assert message.replies[-1] == PROMOTIONAL_WITHDRAW_MESSAGE
    assert db.execute_one("SELECT COUNT(*) FROM account_pool WHERE status = 'available'")[0] == 1

    update, message = _make_update("/withdraw", chat_type="private", user_id=1900)
    asyncio.run(withdraw(update, context))
    assert message.replies[-1] == PROMOTIONAL_WITHDRAW_MESSAGE

    lost = direct_delivery_service.play_slots(1900, "promo", roll=0.49)
    assert lost["wager_source"] == "promotional"
    assert lost["promotional_balance"] == 2
    assert lost["withdrawable_balance"] == 0

    won = direct_delivery_service.play_slots(1900, "promo", roll=0.95)
    assert won["wager_source"] == "promotional"
    assert won["won"] == 6
    assert won["tier"] == "big_win"
    assert won["promotional_balance"] == 1
    assert won["withdrawable_balance"] == 6

    update, message = _make_update("/balance", chat_type="private", user_id=1900)
    asyncio.run(balance(update, context))
    assert "Promotional Credits (non-withdrawable): 1" in message.replies[-1]
    assert "Withdrawable Credits: 6" in message.replies[-1]


def test_promotional_coinflip_profit_and_mixed_source_priority(tmp_path, monkeypatch):
    db = load_app(tmp_path, monkeypatch)
    from services.credit_event_service import credit_event_service
    from services.direct_delivery_service import direct_delivery_service

    credit_event_service.create_event(1, "admin", -1003846885691, 777)
    credit_event_service.claim_current_event(1901, "promo")
    direct_delivery_service.allocate_owed_accounts(1901, 2, "test", prize="2 Accounts")

    loss = direct_delivery_service.play_coinflip(1901, "heads", "promo", roll=0.90)
    assert loss["wager_source"] == "promotional"
    assert loss["promotional_balance"] == 2
    assert loss["withdrawable_balance"] == 2

    win = direct_delivery_service.play_coinflip(1901, "tails", "promo", roll=0.10)
    assert win["wager_source"] == "promotional"
    assert win["payout"] == 1
    assert win["promotional_balance"] == 1
    assert win["withdrawable_balance"] == 3

    final_promo = direct_delivery_service.play_coinflip(1901, "tails", "promo", roll=0.90)
    assert final_promo["wager_source"] == "promotional"
    assert final_promo["promotional_balance"] == 0
    withdrawable_play = direct_delivery_service.play_coinflip(1901, "tails", "promo", roll=0.10)
    assert withdrawable_play["wager_source"] == "withdrawable"
    assert withdrawable_play["withdrawable_balance"] == 4


def test_eventclaim_group_does_not_grant_credits(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.claim_handlers import eventclaim
    from services.credit_event_service import credit_event_service
    from services.direct_delivery_service import direct_delivery_service

    credit_event_service.create_event(1, "admin", -1003846885691, 999)
    update, message = _make_update("/eventclaim", chat_type="supergroup", chat_id=-1003994249946, user_id=1800)
    context = types.SimpleNamespace(args=[], bot=_FakeBot())
    asyncio.run(eventclaim(update, context))

    assert "DMs" in message.replies[-1]
    assert direct_delivery_service.get_pending_amount(1800) == 0
    assert db.execute_one("SELECT COUNT(*) FROM credit_event_claims WHERE telegram_id = ?", (1800,))[0] == 0


def test_fastgive_command_posts_and_persists_without_starting_timer(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from handlers.fastgive_handlers import FASTGIVE_BUTTON_TEXT, fastgive

    class FastgiveBot(_FakeBot):
        async def send_message(self, chat_id, text, reply_markup=None):
            msg = await super().send_message(chat_id, text)
            self.sent[-1] = (chat_id, text, reply_markup)
            return msg

    class FakeApp:
        def __init__(self):
            self.tasks = []
        def create_task(self, coro, name=None):
            self.tasks.append(name)
            coro.close()

    bot = FastgiveBot()
    app = FakeApp()
    update, message = _make_update("/fastgive 10 Accounts", chat_type="private", user_id=1)
    context = types.SimpleNamespace(args=["10", "Accounts"], bot=bot, application=app)

    asyncio.run(fastgive(update, context))

    assert "Fast giveaway started" in message.replies[-1]
    assert bot.sent[0][0] == -1003846885691
    assert "⚡ FLASH GIVEAWAY" in bot.sent[0][1]
    assert "Ends in: 60 Seconds" in bot.sent[0][1]
    assert bot.sent[0][2] is not None
    assert FASTGIVE_BUTTON_TEXT in str(bot.sent[0][2].inline_keyboard[0][0].text)
    row = db.execute_one("SELECT giveaway_id, prize, status, announcement_message_id FROM fast_giveaways")
    assert row[1] == "10 Accounts"
    assert row[2] == "active"
    assert row[3] == 777
    assert app.tasks and app.tasks[0].startswith("fastgive:")


def test_fastgive_rejects_non_admin_and_missing_prize(tmp_path, monkeypatch):
    import asyncio
    import types

    load_app(tmp_path, monkeypatch)
    from handlers.fastgive_handlers import fastgive

    non_admin_update, non_admin_message = _make_update("/fastgive 10 Accounts", chat_type="private", user_id=999)
    context = types.SimpleNamespace(args=["10", "Accounts"], bot=_FakeBot(), application=None)
    asyncio.run(fastgive(non_admin_update, context))
    assert "Unauthorized" in non_admin_message.replies[-1]

    admin_update, admin_message = _make_update("/fastgive", chat_type="private", user_id=1)
    asyncio.run(fastgive(admin_update, context))
    assert admin_message.replies[-1] == "Usage: /fastgive PRIZE"


def test_fastgive_entry_accepts_once_blocks_duplicates_bots_and_expired(tmp_path, monkeypatch):
    import asyncio
    import types

    db = load_app(tmp_path, monkeypatch)
    from services import fastgive_service
    from handlers.fastgive_handlers import fastgive_entry

    gid = "FG-ENTRY1"
    fastgive_service.create_fast_giveaway(
        giveaway_id=gid,
        prize="5,000 Coins",
        creator_id=1,
        creator_name="admin",
        announcement_channel_id=-1003846885691,
        announcement_message_id=10,
    )

    class Query:
        def __init__(self, user):
            self.data = f"fastgive:{gid}"
            self.from_user = user
            self.answers = []
        async def answer(self, text, show_alert=False):
            self.answers.append((text, show_alert))

    user = types.SimpleNamespace(id=222, username="entrant", first_name="En", last_name="Trant", full_name="En Trant", is_bot=False)
    query = Query(user)
    update = types.SimpleNamespace(callback_query=query)
    context = types.SimpleNamespace(bot=_FakeBot())
    asyncio.run(fastgive_entry(update, context))
    assert query.answers[-1][0] == "👍"
    assert db.execute_one("SELECT COUNT(*) FROM fast_giveaway_entries WHERE giveaway_id = ? AND telegram_id = ?", (gid, 222))[0] == 1

    query2 = Query(user)
    asyncio.run(fastgive_entry(types.SimpleNamespace(callback_query=query2), context))
    assert "already" in query2.answers[-1][0]
    assert db.execute_one("SELECT COUNT(*) FROM fast_giveaway_entries WHERE giveaway_id = ?", (gid,))[0] == 1

    bot_user = types.SimpleNamespace(id=333, username="bot", first_name="Bot", last_name=None, full_name="Bot", is_bot=True)
    bot_query = Query(bot_user)
    asyncio.run(fastgive_entry(types.SimpleNamespace(callback_query=bot_query), context))
    assert "Bots cannot enter" in bot_query.answers[-1][0]

    db.execute("UPDATE fast_giveaways SET status = 'ended' WHERE giveaway_id = ?", (gid,))
    db.commit()
    late_user = types.SimpleNamespace(id=444, username="late", first_name="Late", last_name=None, full_name="Late", is_bot=False)
    late_query = Query(late_user)
    asyncio.run(fastgive_entry(types.SimpleNamespace(callback_query=late_query), context))
    assert "closed" in late_query.answers[-1][0]


def test_fastgive_finalize_winner_and_no_entry_cancel(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("ADMIN_LOG_CHANNEL_ID", "-1005555555555")
    db = load_app(tmp_path, monkeypatch)
    from services import fastgive_service
    import handlers.fastgive_handlers as fastgive_handlers

    class FastgiveBot(_FakeBot):
        def __init__(self):
            super().__init__()
            self.edits = []
        async def send_message(self, chat_id, text, reply_markup=None):
            import types
            self.sent.append((chat_id, text, reply_markup))
            return types.SimpleNamespace(message_id=100 + len(self.sent))
        async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
            self.edits.append((chat_id, message_id, text, reply_markup))

    monkeypatch.setattr(fastgive_handlers.fastgive_service, "choose_winner", lambda entries: entries[0] if entries else None)
    bot = FastgiveBot()

    gid = "FG-WINNER"
    fastgive_service.create_fast_giveaway(
        giveaway_id=gid,
        prize="$5 PayPal",
        creator_id=1,
        creator_name="AdminUser",
        announcement_channel_id=-1003846885691,
        announcement_message_id=501,
    )
    fastgive_service.add_entry(gid, 5010, "winner", "Win", "Ner", "Win Ner")
    fastgive_service.add_entry(gid, 5011, "other", "O", "Ther", "O Ther")

    result = asyncio.run(fastgive_handlers.finalize_fastgive(bot, gid))
    assert result["status"] == "ended"
    assert db.execute_one("SELECT status, winner_telegram_id, total_entries FROM fast_giveaways WHERE giveaway_id = ?", (gid,))[:3] == ("ended", 5010, 2)
    assert any("GIVEAWAY ENDED" in edit[2] and "@winner" in edit[2] for edit in bot.edits)
    assert any(sent[0] == 5010 and "Congratulations" in sent[1] for sent in bot.sent)
    assert any(sent[0] == -1003846885691 and "Giveaway Winner" in sent[1] for sent in bot.sent)
    assert any(sent[0] == -1005555555555 and "FAST GIVEAWAY LOG" in sent[1] for sent in bot.sent)

    cancel_gid = "FG-NOENTRY"
    fastgive_service.create_fast_giveaway(
        giveaway_id=cancel_gid,
        prize="CPM2 Account",
        creator_id=1,
        creator_name="AdminUser",
        announcement_channel_id=-1003846885691,
        announcement_message_id=502,
    )
    cancel_result = asyncio.run(fastgive_handlers.finalize_fastgive(bot, cancel_gid))
    assert cancel_result["status"] == "cancelled"
    assert db.execute_one("SELECT status FROM fast_giveaways WHERE giveaway_id = ?", (cancel_gid,))[0] == "cancelled"
    assert any("GIVEAWAY CANCELLED" in edit[2] for edit in bot.edits)
