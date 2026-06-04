import secrets
import string
from config import CLAIM_CODE_PREFIX, CLAIM_CODE_LENGTH

def generate_claim_code() -> str:
    """
    Generate a secure, random claim code.
    Uses secrets module for cryptographic security.
    
    Format: PREFIX-XXXXXX (e.g., CPM-A1B2C3)
    """
    characters = string.ascii_letters + string.digits
    code = ''.join(secrets.choice(characters) for _ in range(CLAIM_CODE_LENGTH))
    return f"{CLAIM_CODE_PREFIX}-{code}"

def validate_claim_code_format(code: str) -> bool:
    """Validate claim code format."""
    if not code or not isinstance(code, str):
        return False
    
    parts = code.split('-')
    if len(parts) != 2:
        return False
    
    prefix, claim_part = parts
    if prefix != CLAIM_CODE_PREFIX:
        return False
    
    if len(claim_part) != CLAIM_CODE_LENGTH:
        return False
    
    return all(c in string.ascii_letters + string.digits for c in claim_part)
