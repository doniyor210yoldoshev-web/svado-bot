import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# --- Database -------------------------------------------------------------
# PostgreSQL is used in production (e.g. postgresql://user:pass@host/db).
# When DATABASE_URL is empty or starts with "sqlite", a local SQLite file is
# used instead. This keeps local development and tests dependency-free while
# production runs on PostgreSQL.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = Path(os.getenv("DB_PATH") or str(DATA_DIR / "market.db"))
FSM_DB_PATH = Path(os.getenv("FSM_DB_PATH") or str(DATA_DIR / "fsm.db"))

# --- Secrets --------------------------------------------------------------
FERNET_KEY_PATH = Path(
    os.getenv("FERNET_KEY_PATH") or str(DATA_DIR / "fernet.key")
)
FERNET_KEY = os.getenv("FERNET_KEY", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# --- Access control -------------------------------------------------------
def _int_list(raw: str) -> list[int]:
    result: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            result.append(int(chunk))
    return result


ADMIN_IDS = _int_list(os.getenv("ADMIN_IDS", ""))

# --- Localization ---------------------------------------------------------
SUPPORTED_LANGS = ("uz", "ru", "en")
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "uz").strip().lower()
if DEFAULT_LANG not in SUPPORTED_LANGS:
    DEFAULT_LANG = "uz"

# --- Payments -------------------------------------------------------------
# manual -> card transfer verified by an admin (default)
# click  -> Click (click.uz) SHOP API with prepare/complete callbacks
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "manual").strip().lower()
PAYMENT_CARD = os.getenv("PAYMENT_CARD", "8600 0000 0000 0000").strip()
PAYMENT_HOLDER = os.getenv("PAYMENT_HOLDER", "Marketplace Admin").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@")

# --- Click (click.uz) -----------------------------------------------------
# Fill these in from the merchant cabinet (merchant.click.uz).
CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "").strip()
CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "").strip()
CLICK_MERCHANT_USER_ID = os.getenv("CLICK_MERCHANT_USER_ID", "").strip()
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "").strip()
CLICK_PAY_URL = os.getenv("CLICK_PAY_URL", "https://my.click.uz/services/pay").strip()
# Public HTTPS base URL of the deployed service (e.g. Render), used in docs.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

# --- Business rules -------------------------------------------------------
COMMISSION_PERCENT = int(os.getenv("COMMISSION_PERCENT", "8"))
CHECK_MINUTES = int(os.getenv("CHECK_MINUTES", "10"))
PENALTY_PERCENT = int(os.getenv("PENALTY_PERCENT", "10"))
PENALTY_INTERVAL_MINUTES = int(os.getenv("PENALTY_INTERVAL_MINUTES", "10"))
AUTO_COMPLETE_MINUTES = int(os.getenv("AUTO_COMPLETE_MINUTES", "60"))
MIN_PRICE = int(os.getenv("MIN_PRICE", "1000"))
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "1000"))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "5000"))
MAX_TOPUP = int(os.getenv("MAX_TOPUP", "50000000"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "10"))

# --- Test mode ------------------------------------------------------------
# Starting fake balance granted to a user when an admin enables test mode.
TEST_INITIAL_BALANCE = int(os.getenv("TEST_INITIAL_BALANCE", "1000000"))
TEST_MAX_ACCOUNTS = int(os.getenv("TEST_MAX_ACCOUNTS", "10"))

SELLER_SHARE_PERCENT = 100 - COMMISSION_PERCENT

if not 0 <= COMMISSION_PERCENT < 100:
    raise ValueError("COMMISSION_PERCENT 0 va 99 oralig'ida bo'lishi kerak")
if CHECK_MINUTES <= 0 or PENALTY_INTERVAL_MINUTES <= 0 or AUTO_COMPLETE_MINUTES < 0:
    raise ValueError("Vaqt sozlamalari noto'g'ri")
if not 0 <= PENALTY_PERCENT <= 100:
    raise ValueError("PENALTY_PERCENT noto'g'ri")
if PAGE_SIZE <= 0 or PAGE_SIZE > 50:
    raise ValueError("PAGE_SIZE 1 va 50 oralig'ida bo'lishi kerak")
