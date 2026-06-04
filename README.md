# TNNR Enterprise Telegram Giveaway Bot

TNNR is a Railway-ready Telegram giveaway automation system with SQLite persistent storage, account-pool inventory, claim codes, audit logging, startup recovery, and admin diagnostics.

## Features

- Multi-admin support through `ADMIN_IDS`.
- Trivia and number-guess giveaway services with duplicate-entry prevention.
- Secure winner and claim-code generation using Python's `secrets` module.
- Account pool upload/import and automatic claim redemption delivery.
- SQLite schema migrations with persistent Railway volume paths.
- Audit logs, pool status, dashboard, health, and diagnostics commands.
- Startup recovery for stale reserved inventory.

## Railway setup

1. Create a Railway project and deploy this repository.
2. Attach a Railway Volume and mount it to the path you set in `RAILWAY_VOLUME_MOUNT_PATH` (recommended: `/data`).
3. Add the environment variables listed below.
4. Set the start command to:

```bash
python main.py
```

5. Make sure the bot is added to the announcement channel and linked discussion group with permission to read group messages, send messages, and send documents.
6. Disable BotFather privacy mode if discussion-group text entry collection is required.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BOT_TOKEN` | Yes | none | Telegram BotFather token. |
| `ADMIN_IDS` | Yes | none | Comma-separated Telegram user IDs that can run admin commands. |
| `ANNOUNCEMENT_CHANNEL_ID` | Yes for announcements | `0` | Telegram channel ID used for public giveaway announcements. |
| `DISCUSSION_GROUP_ID` | Yes for entry collection | `0` | Linked discussion group ID monitored for trivia/guess entries. |
| `ADMIN_LOG_CHANNEL_ID` | Yes for admin alerting | `0` | Private channel/group for admin logs and critical alerts. |
| `LOW_STOCK_ALERT_AMOUNT` | No | `25` | Available-account threshold that triggers low-stock warnings. |
| `CLAIM_CODE_PREFIX` | No | `CPM` | Claim-code prefix, e.g. `CPM-ABC123`. |
| `CLAIM_CODE_LENGTH` | No | `6` | Random claim-code suffix length; minimum recommended is 6. |
| `RAILWAY_VOLUME_MOUNT_PATH` | Yes on Railway | `.` | Persistent volume mount used for database, backups, exports, and logs. |
| `DATABASE_PATH` | No | `$RAILWAY_VOLUME_MOUNT_PATH/giveaways.db` | Optional explicit SQLite database path. |
| `BACKUP_INTERVAL_HOURS` | No | `24` | Intended scheduled backup interval. |
| `BACKUP_RETENTION_DAYS` | No | `30` | Old backup cleanup window. |
| `RESERVED_ACCOUNT_TIMEOUT_HOURS` | No | `24` | Stale reserved accounts older than this are returned to available on startup. |
| `LOG_LEVEL` | No | `INFO` | Python logging level. |

See `.env.example` for a copy/paste template.

## Admin commands

- `/diagnostics` — database, volume, migration, admin, giveaway, and pool health.
- `/health` — lightweight online/health response.
- `/dashboard` — active giveaway, winner, claim, and inventory summary.
- `/giveaway_status` — recent giveaway list and entry counts.
- `/trivia_create question|answer|prize` — create a trivia giveaway.
- `/trivia_draw [GIVEAWAY_ID]` — select a trivia winner.
- `/guess_create min max winning_number prize` — create a number-guess giveaway.
- `/guess_draw [GIVEAWAY_ID]` — select a number-guess winner.
- `/giveaway_stop GIVEAWAY_ID` — stop a giveaway.
- `/admin_upload_pool` — request a `.txt` pool upload in `email:password` format.
- `/pool_add_single email@example.com:password` — add one account to the pool.
- `/pool_status` — inventory status counts.
- `/pool_mark_invalid email@example.com` — mark an account invalid.
- `/claimcode CPM-XXXXXX` — admins can look up a code; users redeem their own code.

## Database tables

The migration system creates: `users`, `admins`, `giveaways`, `entries`, `winners`, `claim_codes`, `account_pool`, `redemptions`, `audit_logs`, `system_logs`, and `schema_migrations`.

## Inventory upload format

Use one account per line:

```text
account1@example.com:password1
account2@example.com:password2
```

Malformed lines, duplicate emails, and invalid email addresses are skipped. Passwords are stored for prize delivery and are not printed to logs.

## Claim process

1. A giveaway service selects a winner and creates a unique claim code.
2. The winner runs `/claimcode CPM-XXXXXX`.
3. The bot validates ownership, stock, and previous redemption state.
4. Accounts are reserved before delivery and marked delivered only after the redemption flow completes.
5. Redemptions and critical actions are written to the audit log.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest -q
python main.py
```

`BOT_TOKEN` is required to actually run the Telegram polling process. Unit tests use a temporary database and do not require Telegram credentials.
