"""Security primitives: encryption, hashing, validation and safe output."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from cryptography.fernet import Fernet, InvalidToken

from config import ADMIN_IDS, FERNET_KEY, FERNET_KEY_PATH
from utils import clean_card, esc, parse_amount

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None

SELLER_CODE_LENGTH = 6
SELLER_CODE_MIN = 100_000
SELLER_CODE_MAX = 999_999

_PBKDF2_ITERATIONS = 240_000


def _load_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    if FERNET_KEY:
        key = FERNET_KEY.encode("ascii")
    elif FERNET_KEY_PATH.exists():
        key = FERNET_KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        FERNET_KEY_PATH.write_bytes(key)
        try:
            FERNET_KEY_PATH.chmod(0o600)
        except OSError:
            pass
    _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    return _load_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _load_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logger.error("Shifrlangan ma'lumotni ochishda xatolik: %s", exc)
        raise ValueError("Ma'lumotni shifrdan ochib bo'lmadi") from exc


def login_hash(login: str) -> str:
    normalized = login.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 with a random salt. Never store plain passwords."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), expected)


def generate_seller_code() -> str:
    """Generate a unique-looking 6-digit numeric seller ID."""
    return str(secrets.randbelow(SELLER_CODE_MAX - SELLER_CODE_MIN + 1) + SELLER_CODE_MIN)


def generate_reference(prefix: str) -> str:
    return f"{prefix}:{secrets.token_hex(12)}"


def is_admin(tg_id: int | None) -> bool:
    return tg_id is not None and int(tg_id) in ADMIN_IDS


def validate_amount(value: str | None, minimum: int, maximum: int | None = None) -> int | None:
    amount = parse_amount(value)
    if amount is None or amount < minimum:
        return None
    if maximum is not None and amount > maximum:
        return None
    return amount


def valid_card(value: str | None) -> str | None:
    card = clean_card(value)
    return card if 13 <= len(card) <= 19 else None


def normalize_text(value: str | None, max_length: int = 200) -> str:
    text = (value or "").strip()
    return text[:max_length]


def safe(value: object) -> str:
    """Escape untrusted text before inserting it into HTML messages."""
    return esc(value)


def is_numeric_id(value: str | None) -> bool:
    return bool(value) and bool(re.fullmatch(r"\d{1,12}", (value or "").strip()))
