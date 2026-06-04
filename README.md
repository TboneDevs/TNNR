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

## Runtime compatibility

This bot is pinned for production on **Python 3.11.9** with `python-telegram-bot==20.7`. Railway must not build it on Python 3.13, because the pinned Telegram library is only classified for Python 3.8 through 3.12 and can crash during `Application.builder().build()` while constructing PTB internals.

The repository includes three safeguards:

- `runtime.txt` pins `python-3.11.9` for Python buildpack/Nixpacks detection.
- `nixpacks.toml` keeps the Nixpacks install/start flow on Python 3.11.
- `Dockerfile` uses `python:3.11.9-slim` as the authoritative Railway build path when Dockerfile deployment is enabled.

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
| `ANNOUNCEMENT_CHANNEL_ID` | Yes for announcements | `-1003846885691` | Telegram channel ID used for public giveaway announcements (`@TnnrCPM`). |
| `DISCUSSION_GROUP_ID` | Yes for entry collection | `-1003994249946` | Linked discussion group ID monitored for trivia/guess entries and linked channel comments. |
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
- `/trivia_create question|answer|prize` — create a trivia giveaway and post the announcement to `@TnnrCPM`.
- `/trivia_draw [GIVEAWAY_ID]` — select a trivia winner.
- `/guess_create min max winning_number prize` — create a number-guess giveaway and post the announcement to `@TnnrCPM`.
- `/guess_draw [GIVEAWAY_ID]` — select a number-guess winner.
- `/spin_create win_odds prize` — create a spin giveaway and post the announcement to `@TnnrCPM`; odds may be `0.25` or `25`.
- `/channeltest` — admin-only announcement channel posting test for `@TnnrCPM`.
- `/discussiontest` — admin-only linked discussion group access/send/read test for `DISCUSSION_GROUP_ID`.
- `/giveaway_stop GIVEAWAY_ID` — stop a giveaway.
- `/admin_upload_pool` — request a `.txt` pool upload in `email:password` format.
- `/pool_add_single email@example.com:password` — add one account to the pool.
- `/pool_status` — inventory status counts.
- `/pool_mark_invalid email@example.com` — mark an account invalid.
- `/claimcode CPM-XXXXXX` — admins can look up a code; users redeem their own code.

## Giveaway announcement channel flow

Giveaway creation commands are intentionally run from a bot DM or the private admin log channel, not from the public announcement channel, public groups, or the linked discussion group. After validation, the bot verifies it can access/post to `@TnnrCPM` (`ANNOUNCEMENT_CHANNEL_ID=-1003846885691`) and, for trivia/number guess flows, verifies access to the linked discussion group (`DISCUSSION_GROUP_ID=-1003994249946`). It then posts the announcement, stores `announcement_channel_id`, `announcement_message_id`, and `discussion_group_id` on the giveaway row, and confirms the giveaway ID plus Telegram message ID back to the admin.

If channel posting fails, the bot does not create the giveaway and replies with one of the deployment/debug categories: `BOT_NOT_ADMIN`, `CHANNEL_NOT_FOUND`, `TELEGRAM_API_ERROR`, or `INSUFFICIENT_PERMISSIONS`.

Run `/channeltest` and `/discussiontest` in a bot DM or the admin log channel after deployment. `/channeltest` confirms the bot can post to `@TnnrCPM`; `/discussiontest` confirms access/send capability and starts the live read test phrase. A failure returns the exact Telegram error category and message.

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
2. Public winner announcements identify the winner by available name, username, and Telegram ID, but never include the claim code.
3. The bot sends the claim code and `/claimcode CPM-XXXXXX` redemption instruction only to the winner by private DM.
4. The winner must redeem the code in a private DM with the bot; public/group redemption attempts are blocked to keep claim codes private.
5. The bot validates ownership, stock, and previous redemption state.
6. Accounts are reserved before delivery and marked delivered only after the redemption flow completes.
7. Redemptions and critical actions are written to the audit log.

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


### Discussion group entry handling

Trivia and number guess entries are collected silently from `DISCUSSION_GROUP_ID=-1003994249946`, including normal linked-discussion messages and channel comments routed through that discussion group. Trivia answers are normalized by stripping, lowercasing, and collapsing repeated spaces before comparison. Correct trivia entries and valid in-range number guesses are stored with source metadata and duplicates/incorrect/invalid submissions are ignored silently.

Admin tests:

- `/channeltest` posts `✅ Channel Test Successful` to `@TnnrCPM` and returns the channel message ID.
- `/discussiontest` verifies discussion group access, sends `✅ Discussion Group Test Successful`, and starts the live read phrase test. Send `test trivia access` in the discussion group or as a channel comment to confirm the bot can read routed comments/messages.
