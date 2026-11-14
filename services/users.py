"""User and seller domain logic."""

from __future__ import annotations

import logging

from config import ADMIN_IDS, DEFAULT_LANG, SUPPORTED_LANGS
from database.db import db
from services.errors import ServiceError, ValidationError
from services.security import generate_seller_code
from utils import utcnow_iso

logger = logging.getLogger(__name__)


async def ensure_user(tg_id: int, full_name: str, username: str | None) -> dict:
    is_admin = 1 if int(tg_id) in ADMIN_IDS else 0
    row = await db.fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    if row is None:
        await db.insert(
            "INSERT INTO users (tg_id, full_name, username, is_admin, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tg_id, full_name or "", username, is_admin, utcnow_iso()),
        )
    else:
        await db.execute(
            "UPDATE users SET full_name = ?, username = ?, is_admin = ?, updated_at = ? "
            "WHERE id = ?",
            (full_name or "", username, is_admin, utcnow_iso(), row["id"]),
        )
    return await db.fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))


async def get_user_by_tg(tg_id: int) -> dict | None:
    return await db.fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))


async def get_user(user_id: int) -> dict | None:
    return await db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


async def get_user_by_seller_code(seller_code: str) -> dict | None:
    return await db.fetchone(
        "SELECT * FROM users WHERE seller_code = ? AND is_seller = 1 AND is_blocked = 0",
        (seller_code.strip(),),
    )


async def set_lang(user_id: int, lang: str) -> None:
    if lang not in SUPPORTED_LANGS:
        raise ValidationError("Noto'g'ri til")
    await db.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user_id))


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 9:
        raise ValidationError("Telefon raqami noto'g'ri")
    return "+" + digits


async def _ensure_phone_free(phone: str, user_id: int) -> None:
    row = await db.fetchone(
        "SELECT id FROM users WHERE phone = ? AND id <> ?", (phone, user_id)
    )
    if row is not None:
        raise ServiceError("Bu telefon raqami allaqachon ishlatilgan")


async def set_phone(user_id: int, phone: str, verified: bool = True) -> None:
    normalized = normalize_phone(phone)
    await _ensure_phone_free(normalized, user_id)
    await db.execute(
        "UPDATE users SET phone = ?, phone_verified = ?, updated_at = ? WHERE id = ?",
        (normalized, 1 if verified else 0, utcnow_iso(), user_id),
    )


async def accept_terms(user_id: int) -> None:
    await db.execute("UPDATE users SET terms_accepted = 1 WHERE id = ?", (user_id,))


def user_lang(user: dict | None) -> str:
    if user and user.get("lang") in SUPPORTED_LANGS:
        return user["lang"]
    return DEFAULT_LANG


async def _unique_seller_code() -> str:
    for _ in range(50):
        candidate = generate_seller_code()
        exists = await db.fetchone(
            "SELECT 1 AS found FROM users WHERE seller_code = ?", (candidate,)
        )
        if not exists:
            return candidate
    raise ServiceError("Sotuvchi ID yaratib bo'lmadi. Keyinroq urinib ko'ring.")


async def register_seller(user_id: int, phone: str) -> dict:
    user = await get_user(user_id)
    if user is None:
        raise ServiceError("Foydalanuvchi topilmadi")
    if user["is_seller"]:
        return user
    normalized = normalize_phone(phone)
    await _ensure_phone_free(normalized, user_id)
    seller_code = await _unique_seller_code()
    await db.execute(
        "UPDATE users SET phone = ?, phone_verified = 1, seller_code = ?, is_seller = 1, "
        "updated_at = ? WHERE id = ?",
        (normalized, seller_code, utcnow_iso(), user_id),
    )
    return await get_user(user_id)


async def seller_stats(seller_id: int) -> dict[str, int]:
    rows = await db.fetchall(
        "SELECT status, COUNT(*) AS c FROM accounts WHERE seller_id = ? GROUP BY status",
        (seller_id,),
    )
    stats = {"active": 0, "paused": 0, "sold": 0, "refunded": 0}
    for row in rows:
        if row["status"] in stats:
            stats[row["status"]] = int(row["c"])
    return stats


async def seller_rating(seller_id: int) -> str:
    row = await db.fetchone(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'refunded' THEN 1 ELSE 0 END) AS refunded "
        "FROM orders WHERE seller_id = ? AND status IN "
        "('completed','auto_completed','refunded')",
        (seller_id,),
    )
    total = int(row["total"] or 0) if row else 0
    if total == 0:
        return "—"
    refunded = int(row["refunded"] or 0) if row else 0
    rating = max(1.0, 5.0 - 4.0 * refunded / total)
    return f"{rating:.1f} ⭐ ({total})"


async def search_user(query: str) -> dict | None:
    query = query.strip().lstrip("@")
    if not query:
        return None
    if query.isdigit():
        return await db.fetchone(
            "SELECT * FROM users WHERE tg_id = ? OR id = ? OR seller_code = ?",
            (int(query), int(query), query),
        )
    return await db.fetchone(
        "SELECT * FROM users WHERE username = ? OR seller_code = ?",
        (query, query),
    )


async def set_blocked(user_id: int, blocked: bool) -> None:
    await db.execute(
        "UPDATE users SET is_blocked = ? WHERE id = ?", (1 if blocked else 0, user_id)
    )


async def set_verified(user_id: int, verified: bool) -> None:
    await db.execute(
        "UPDATE users SET is_verified = ? WHERE id = ?", (1 if verified else 0, user_id)
    )


async def toggle_test_mode(user_id: int, initial_balance: int) -> dict:
    user = await get_user(user_id)
    if user is None:
        raise ServiceError("Foydalanuvchi topilmadi")
    enabled = not bool(user["test_mode"])
    if enabled and int(user["test_balance"]) <= 0:
        await db.execute(
            "UPDATE users SET test_mode = 1, test_balance = ? WHERE id = ?",
            (initial_balance, user_id),
        )
    else:
        await db.execute(
            "UPDATE users SET test_mode = ? WHERE id = ?", (1 if enabled else 0, user_id)
        )
    return await get_user(user_id)


async def broadcast_recipients() -> list[dict]:
    return await db.fetchall(
        "SELECT tg_id, lang FROM users WHERE is_blocked = 0 AND tg_id > 0"
    )


async def admin_langs() -> list[tuple[int, str]]:
    """Return (tg_id, lang) for every configured admin that exists in the DB."""
    result: list[tuple[int, str]] = []
    for admin_id in ADMIN_IDS:
        user = await get_user_by_tg(admin_id)
        result.append((admin_id, user_lang(user)))
    return result
