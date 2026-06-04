import logging
import re
from datetime import datetime

from database.database import db
from services.pool_service import pool_service
from utils.audit_logger import audit_log
from utils.claimcode import claim_code_search_key, is_plausible_claim_code, normalize_claim_code

logger = logging.getLogger('tnnr.services.claim')

REDEEMED_STATUSES = {'redeemed', 'claimed', 'delivered'}


class ClaimService:
    """Handles claim-code listing, validation, redemption, and account delivery."""

    @staticmethod
    def _winner_select_sql() -> str:
        return """SELECT id, claim_code, giveaway_id, telegram_id, username, display_name,
                         prize, claimed_status, created_at, claimed_at
                  FROM winners"""

    @staticmethod
    def _claim_code_sql_key(column: str) -> str:
        return f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(UPPER({column}), '-', ''), '_', ''), ' ', ''), char(10), ''), char(13), '')"

    @staticmethod
    def _find_winner_by_code(claim_code: str):
        """Find a winner by exact, canonical, compact, or claim_codes-table match."""
        canonical_code = normalize_claim_code(claim_code)
        search_key = claim_code_search_key(canonical_code or claim_code)
        winner = None

        if canonical_code:
            winner = db.execute_one(
                ClaimService._winner_select_sql() + " WHERE UPPER(claim_code) = ?",
                (canonical_code,),
            )

        if not winner and search_key:
            winner = db.execute_one(
                ClaimService._winner_select_sql()
                + f" WHERE {ClaimService._claim_code_sql_key('claim_code')} = ?",
                (search_key,),
            )

        if not winner and search_key:
            winner = db.execute_one(
                ClaimService._winner_select_sql()
                + " WHERE id = ("
                + "SELECT winner_id FROM claim_codes "
                + f"WHERE winner_id IS NOT NULL AND {ClaimService._claim_code_sql_key('code')} = ? "
                + "ORDER BY id DESC LIMIT 1)",
                (search_key,),
            )

        # Final Python-side fallback handles Unicode dash/zero-width artifacts in
        # old DB rows that SQLite REPLACE expressions cannot normalize.
        if not winner and search_key:
            for row in db.execute_all(ClaimService._winner_select_sql()):
                if claim_code_search_key(row[1]) == search_key:
                    winner = row
                    break

        if not winner and search_key:
            for row in db.execute_all("SELECT winner_id, code FROM claim_codes WHERE winner_id IS NOT NULL ORDER BY id DESC"):
                if claim_code_search_key(row[1]) == search_key:
                    winner = db.execute_one(ClaimService._winner_select_sql() + " WHERE id = ?", (row[0],))
                    if winner:
                        break

        return canonical_code, search_key, winner

    @staticmethod
    def _claim_code_status(winner_id: int, stored_claim_code: str) -> str:
        search_key = claim_code_search_key(stored_claim_code)
        row = db.execute_one(
            """SELECT status FROM claim_codes
               WHERE winner_id = ?
                  OR REPLACE(REPLACE(REPLACE(UPPER(code), '-', ''), '_', ''), ' ', '') = ?
               ORDER BY id DESC LIMIT 1""",
            (winner_id, search_key),
        )
        return (row[0] if row and row[0] else 'unclaimed').lower()

    @staticmethod
    def _parse_account_count(prize_text: str) -> int | None:
        """Extract the number of accounts from public prize text."""
        match = re.search(r"\b(\d+)\b", prize_text or "")
        if not match:
            return None
        count = int(match.group(1))
        return count if count > 0 else None

    @staticmethod
    def list_unclaimed_codes(telegram_id: int) -> list[dict]:
        """Return lookup-only unclaimed claim codes belonging to one Telegram ID."""
        rows = db.execute_all(
            """SELECT w.id, w.claim_code, w.prize, w.giveaway_id, w.created_at,
                      COALESCE(g.type, 'giveaway') AS giveaway_type
               FROM winners w
               LEFT JOIN giveaways g ON g.giveaway_id = w.giveaway_id
               WHERE w.telegram_id = ?
                 AND COALESCE(w.claimed_status, 0) = 0
               ORDER BY w.created_at ASC""",
            (telegram_id,),
        )
        codes = []
        for row in rows:
            status = ClaimService._claim_code_status(row[0], row[1])
            if status in REDEEMED_STATUSES:
                continue
            codes.append({
                'winner_id': row[0],
                'claim_code': normalize_claim_code(row[1]) or row[1],
                'stored_claim_code': row[1],
                'prize': row[2],
                'giveaway_id': row[3],
                'won_at': row[4],
                'giveaway_type': (row[5] or 'giveaway').title(),
            })
        return codes

    @staticmethod
    def validate_claim_code(claim_code: str, telegram_id: int) -> dict:
        """
        Validate claim code ownership and status.
        Returns: {'valid': bool, 'message': str, 'winner': dict}
        """
        try:
            canonical_code, search_key, winner = ClaimService._find_winner_by_code(claim_code)

            if not winner:
                if canonical_code or is_plausible_claim_code(claim_code):
                    message = '❌ Claim code not found.\n\nRun /mycodes to view your unclaimed claim codes.'
                else:
                    message = '❌ Invalid claim code.\n\nExample format:\nCPM-ABC123\n\nYou can also run /mycodes to view your available codes.'
                logger.info("Claim code lookup failed: canonical=%s search_key_present=%s plausible=%s", canonical_code, bool(search_key), is_plausible_claim_code(claim_code))
                return {'valid': False, 'message': message, 'winner': None}

            status = ClaimService._claim_code_status(winner[0], winner[1])
            is_redeemed = winner[7] == 1 or status in REDEEMED_STATUSES

            if winner[3] != telegram_id:
                logger.warning("Ownership validation failed: %s tried to claim code for %s", telegram_id, winner[3])
                return {
                    'valid': False,
                    'message': '❌ This claim code belongs to another Telegram account.\n\nPlease use the same Telegram account that won the giveaway.',
                    'winner': None,
                }

            if is_redeemed:
                return {
                    'valid': False,
                    'message': '⚠️ This claim code has already been redeemed.',
                    'winner': None,
                }

            return {
                'valid': True,
                'message': 'Claim code valid',
                'winner': {
                    'id': winner[0],
                    'claim_code': winner[1],
                    'giveaway_id': winner[2],
                    'telegram_id': winner[3],
                    'username': winner[4],
                    'display_name': winner[5],
                    'prize': winner[6],
                    'claimed_status': winner[7],
                    'created_at': winner[8],
                    'claimed_at': winner[9],
                },
            }
        except Exception as e:
            logger.error("Claim validation failed: %s", e)
            return {'valid': False, 'message': f'Validation error: {str(e)}', 'winner': None}

    @staticmethod
    def redeem_claim_code(claim_code: str, telegram_id: int, username: str) -> dict:
        """
        Redeem a claim code and deliver accounts.
        Returns: {'success': bool, 'accounts': list, 'message': str, 'claim_code': str}
        """
        try:
            validation = ClaimService.validate_claim_code(claim_code, telegram_id)

            if not validation['valid']:
                return {
                    'success': False,
                    'accounts': [],
                    'message': validation['message'],
                    'claim_code': None,
                }

            winner = validation['winner']
            stored_claim_code = winner['claim_code']
            prize_text = winner['prize']
            account_count = ClaimService._parse_account_count(prize_text)
            if account_count is None:
                return {
                    'success': False,
                    'accounts': [],
                    'message': 'Invalid prize format',
                    'claim_code': stored_claim_code,
                }

            accounts = pool_service.get_available_accounts(account_count)

            if len(accounts) < account_count:
                account_ids = [acc[0] for acc in accounts]
                if account_ids:
                    pool_service.revert_reserved_accounts(account_ids)
                return {
                    'success': False,
                    'accounts': [],
                    'message': 'Not enough accounts available in inventory',
                    'claim_code': stored_claim_code,
                }

            account_ids = [acc[0] for acc in accounts]
            if not pool_service.mark_accounts_delivered(stored_claim_code, account_ids):
                pool_service.revert_reserved_accounts(account_ids)
                return {
                    'success': False,
                    'accounts': [],
                    'message': 'Failed to deliver accounts',
                    'claim_code': stored_claim_code,
                }

            redeemed_at = datetime.now()
            db.execute(
                """UPDATE winners
                   SET claimed_status = 1, claimed_at = ?
                   WHERE id = ?""",
                (redeemed_at, winner['id']),
            )
            db.execute(
                """INSERT INTO redemptions
                   (claim_code, telegram_id, prize, accounts_delivered, redeemed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (stored_claim_code, telegram_id, prize_text, account_count, redeemed_at),
            )
            db.execute(
                """UPDATE claim_codes
                   SET status = 'redeemed', redeemed_at = ?
                   WHERE winner_id = ?
                      OR REPLACE(REPLACE(REPLACE(UPPER(code), '-', ''), '_', ''), ' ', '') = ?""",
                (redeemed_at, winner['id'], claim_code_search_key(stored_claim_code)),
            )
            db.commit()

            audit_log.log(
                action='CLAIM_REDEEMED',
                actor_id=telegram_id,
                actor_name=username,
                details=f"Prize: {prize_text}, Code: {stored_claim_code}, Accounts delivered: {account_count}",
                result='SUCCESS',
            )

            logger.info("Claim redeemed: %s by %s", stored_claim_code, telegram_id)

            formatted_accounts = [f"{acc[1]}:{acc[2]}" for acc in accounts]

            return {
                'success': True,
                'accounts': formatted_accounts,
                'message': 'Prize delivered successfully',
                'claim_code': stored_claim_code,
                'prize': prize_text,
                'accounts_delivered': account_count,
            }
        except Exception as e:
            logger.error("Claim redemption failed: %s", e)
            return {'success': False, 'accounts': [], 'message': f'Redemption error: {str(e)}', 'claim_code': None}

    @staticmethod
    def get_claim_status(claim_code: str) -> dict:
        """Get status of a claim code using the same normalization as redemption."""
        try:
            _, _, winner = ClaimService._find_winner_by_code(claim_code)
            if not winner:
                return {'found': False}

            return {
                'found': True,
                'code': winner[1],
                'winner_id': winner[3],
                'winner': winner[4],
                'prize': winner[6],
                'claimed': winner[7] == 1 or ClaimService._claim_code_status(winner[0], winner[1]) in REDEEMED_STATUSES,
                'claimed_at': winner[9],
            }
        except Exception as e:
            logger.error("Failed to get claim status: %s", e)
            return {'found': False}


claim_service = ClaimService()
