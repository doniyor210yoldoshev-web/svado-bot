"""Wallet, top-up and withdrawal logic with a pluggable payment provider."""

from __future__ import annotations

import logging

from config import (
    MAX_TOPUP,
    MIN_TOPUP,
    MIN_WITHDRAW,
    PAYMENT_CARD,
    PAYMENT_HOLDER,
    PAYMENT_PROVIDER,
)
from database.db import db
from services import security
from services.errors import InsufficientFunds, ServiceError
from utils import fmt_sum, utcnow_iso

logger = logging.getLogger(__name__)

RECEIPT_TYPES = ("photo", "document", "text")


async def wallet_summary(user: dict) -> dict:
    earned = await db.fetchone(
        "SELECT COALESCE(SUM(amount),0) AS total FROM transactions "
        "WHERE user_id = ? AND type = 'sale' AND mode = 'real'",
        (user["id"],),
    )
    spent = await db.fetchone(
        "SELECT COALESCE(SUM(-amount),0) AS total FROM transactions "
        "WHERE user_id = ? AND type = 'purchase' AND mode = 'real'",
        (user["id"],),
    )
    pending_out = await db.fetchone(
        "SELECT COALESCE(SUM(amount),0) AS total FROM withdrawals "
        "WHERE user_id = ? AND status = 'pending'",
        (user["id"],),
    )
    return {
        "balance": int(user["balance"]),
        "test_balance": int(user["test_balance"]),
        "test_mode": bool(user["test_mode"]),
        "earned": int(earned["total"]) if earned else 0,
        "spent": int(spent["total"]) if spent else 0,
        "pending_withdrawals": int(pending_out["total"]) if pending_out else 0,
    }


async def list_transactions(user_id: int, mode: str = "real", limit: int = 15) -> list[dict]:
    return await db.fetchall(
        "SELECT * FROM transactions WHERE user_id = ? AND mode = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, mode, limit),
    )


# --------------------------------------------------------------------------
# Top-ups
# --------------------------------------------------------------------------
def payment_instructions() -> dict:
    if PAYMENT_PROVIDER == "manual":
        return {"provider": "manual", "card": PAYMENT_CARD, "holder": PAYMENT_HOLDER}
    raise ServiceError(
        "To'lov provayderi sozlanmagan. Iltimos, qo'llab-quvvatlash xizmatiga murojaat qiling."
    )


async def create_topup(
    user_id: int, amount: int, receipt_type: str, receipt_value: str
) -> dict:
    if amount < MIN_TOPUP or amount > MAX_TOPUP:
        raise ServiceError(
            f"To'ldirish miqdori {fmt_sum(MIN_TOPUP)} va {fmt_sum(MAX_TOPUP)} orasida bo'lishi kerak"
        )
    if receipt_type not in RECEIPT_TYPES or not (receipt_value or "").strip():
        raise ServiceError("To'lov chekini yuborishingiz kerak")
    payment_instructions()
    topup_id = await db.insert(
        "INSERT INTO topups "
        "(user_id, amount, receipt_type, receipt_value, status, mode, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', 'real', ?)",
        (user_id, amount, receipt_type, receipt_value.strip(), utcnow_iso()),
    )
    return await db.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))


async def create_click_topup(user_id: int, amount: int) -> dict:
    """Create a pending top-up that will be confirmed by Click's callback."""
    if amount < MIN_TOPUP or amount > MAX_TOPUP:
        raise ServiceError(
            f"To'ldirish miqdori {fmt_sum(MIN_TOPUP)} va {fmt_sum(MAX_TOPUP)} orasida bo'lishi kerak"
        )
    topup_id = await db.insert(
        "INSERT INTO topups "
        "(user_id, amount, receipt_type, receipt_value, status, mode, created_at) "
        "VALUES (?, ?, 'click', '', 'pending', 'real', ?)",
        (user_id, amount, utcnow_iso()),
    )
    row = await db.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))
    if row is None:
        raise ServiceError("To'lov yaratib bo'lmadi. Keyinroq urinib ko'ring.")
    return row


async def get_click_topup(topup_id: int) -> dict | None:
    row = await get_topup(topup_id)
    if row is None or row.get("receipt_type") != "click":
        return None
    return row


async def mark_click_prepared(topup_id: int, click_trans_id: str) -> None:
    await db.execute(
        "UPDATE topups SET receipt_value = ? WHERE id = ? AND status = 'pending'",
        ((click_trans_id or "click")[:200], topup_id),
    )


async def cancel_click_topup(topup_id: int) -> None:
    await db.execute(
        "UPDATE topups SET status = 'rejected', reviewed_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (utcnow_iso(), topup_id),
    )


async def complete_click_topup(
    topup_id: int, click_trans_id: str = "", click_paydoc_id: str = ""
) -> dict | None:
    """Credit the user's wallet once. Returns None for unknown/foreign top-ups."""
    async with db.transaction() as ex:
        topup = await ex.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))
        if topup is None or topup["receipt_type"] != "click":
            return None
        user = await ex.fetchone(
            "SELECT tg_id, lang FROM users WHERE id = ?", (topup["user_id"],)
        )
        info = {
            "topup_id": topup_id,
            "amount": int(topup["amount"]),
            "user_tg": user["tg_id"] if user else None,
            "user_lang": user["lang"] if user else None,
        }
        if topup["status"] == "approved":
            info["already"] = True
            return info
        if topup["status"] != "pending":
            return None

        updated = await ex.execute(
            "UPDATE topups SET status = 'approved', reviewed_by = 0, reviewed_at = ?, "
            "receipt_value = ? WHERE id = ? AND status = 'pending'",
            (
                utcnow_iso(),
                (click_trans_id or "click")[:200],
                topup_id,
            ),
        )
        if updated == 0:
            info["already"] = True
            return info

        await ex.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (int(topup["amount"]), topup["user_id"]),
        )
        description = f"Click to'lov #{topup_id} (click_trans_id: {click_trans_id})"
        if click_paydoc_id:
            description += f", paydoc: {click_paydoc_id}"
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, 'topup', 'real', ?, ?, ?)",
            (
                topup["user_id"],
                int(topup["amount"]),
                f"topup:{topup_id}",
                description[:500],
                utcnow_iso(),
            ),
        )
        return info


async def list_user_topups(user_id: int, limit: int = 10) -> list[dict]:
    return await db.fetchall(
        "SELECT * FROM topups WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )


async def list_pending_topups(limit: int = 10) -> list[dict]:
    return await db.fetchall(
        "SELECT t.*, u.tg_id AS user_tg, u.full_name AS user_name, "
        "u.seller_code AS seller_code, u.username AS username, u.lang AS user_lang "
        "FROM topups t JOIN users u ON u.id = t.user_id "
        "WHERE t.status = 'pending' AND t.receipt_type != 'click' "
        "ORDER BY t.id ASC LIMIT ?",
        (limit,),
    )


async def get_topup(topup_id: int) -> dict | None:
    return await db.fetchone(
        "SELECT t.*, u.tg_id AS user_tg, u.full_name AS user_name, u.lang AS user_lang "
        "FROM topups t JOIN users u ON u.id = t.user_id WHERE t.id = ?",
        (topup_id,),
    )


async def approve_topup(topup_id: int, admin_tg: int) -> dict:
    async with db.transaction() as ex:
        topup = await ex.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))
        if topup is None:
            raise ServiceError("To'lov topilmadi")
        if topup["status"] != "pending":
            raise ServiceError("Bu to'lov allaqachon ko'rib chiqilgan")
        await ex.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (topup["amount"], topup["user_id"]),
        )
        await ex.execute(
            "UPDATE topups SET status = 'approved', reviewed_by = ?, reviewed_at = ? "
            "WHERE id = ?",
            (admin_tg, utcnow_iso(), topup_id),
        )
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, 'topup', 'real', ?, ?, ?)",
            (
                topup["user_id"],
                int(topup["amount"]),
                f"topup:{topup_id}",
                f"Balans to'ldirildi (to'lov #{topup_id})",
                utcnow_iso(),
            ),
        )
        user = await ex.fetchone(
            "SELECT tg_id, lang FROM users WHERE id = ?", (topup["user_id"],)
        )
        return {
            "topup_id": topup_id,
            "amount": int(topup["amount"]),
            "user_tg": user["tg_id"] if user else None,
            "user_lang": user["lang"] if user else None,
        }


async def reject_topup(topup_id: int, admin_tg: int, reason: str = "") -> dict:
    async with db.transaction() as ex:
        topup = await ex.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))
        if topup is None:
            raise ServiceError("To'lov topilmadi")
        if topup["status"] != "pending":
            raise ServiceError("Bu to'lov allaqachon ko'rib chiqilgan")
        await ex.execute(
            "UPDATE topups SET status = 'rejected', reviewed_by = ?, reviewed_at = ? "
            "WHERE id = ?",
            (admin_tg, utcnow_iso(), topup_id),
        )
        user = await ex.fetchone(
            "SELECT tg_id, lang FROM users WHERE id = ?", (topup["user_id"],)
        )
        return {
            "topup_id": topup_id,
            "amount": int(topup["amount"]),
            "reason": reason,
            "user_tg": user["tg_id"] if user else None,
            "user_lang": user["lang"] if user else None,
        }


# --------------------------------------------------------------------------
# Withdrawals
# --------------------------------------------------------------------------
async def create_withdrawal(user_id: int, amount: int, card_number: str) -> dict:
    if amount < MIN_WITHDRAW:
        raise ServiceError(f"Minimal yechish miqdori: {fmt_sum(MIN_WITHDRAW)}")
    card = security.valid_card(card_number)
    if card is None:
        raise ServiceError("Karta raqami noto'g'ri")

    async with db.transaction() as ex:
        user = await ex.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        if user is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        if user["test_mode"]:
            raise ServiceError("Test rejimida pul yechib bo'lmaydi")
        if int(user["balance"]) < amount:
            raise InsufficientFunds(amount, int(user["balance"]))
        debited = await ex.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        if debited == 0:
            raise InsufficientFunds(amount, int(user["balance"]))
        withdrawal_id = await ex.insert(
            "INSERT INTO withdrawals "
            "(user_id, amount, card_number, status, mode, created_at) "
            "VALUES (?, ?, ?, 'pending', 'real', ?)",
            (user_id, amount, card, utcnow_iso()),
        )
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, 'withdraw', 'real', ?, ?, ?)",
            (
                user_id,
                -amount,
                f"withdrawal:{withdrawal_id}:hold",
                f"Pul yechish so'rovi #{withdrawal_id} (zaxiraga olindi)",
                utcnow_iso(),
            ),
        )
        return await ex.fetchone("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))


async def admin_adjust_balance(
    user_id: int, amount: int, note: str, admin_tg: int
) -> tuple[dict, int]:
    if amount == 0:
        raise ServiceError("Summa nolga teng bo'lishi mumkin emas")
    async with db.transaction() as ex:
        user = await ex.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        if user is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        new_balance = int(user["balance"]) + amount
        if new_balance < 0:
            raise ServiceError("Balans manfiy bo'lib qoladi")
        await ex.execute(
            "UPDATE users SET balance = ? WHERE id = ?", (new_balance, user_id)
        )
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, 'adjustment', 'real', ?, ?, ?)",
            (
                user_id,
                amount,
                security.generate_reference("adjustment"),
                (note or f"Admin {admin_tg} tomonidan tuzatish")[:500],
                utcnow_iso(),
            ),
        )
        updated = await ex.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        if updated is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        return updated, new_balance


async def list_user_withdrawals(user_id: int, limit: int = 10) -> list[dict]:
    return await db.fetchall(
        "SELECT * FROM withdrawals WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )


async def list_pending_withdrawals(limit: int = 10) -> list[dict]:
    return await db.fetchall(
        "SELECT w.*, u.tg_id AS user_tg, u.full_name AS user_name, "
        "u.seller_code AS seller_code, u.username AS username, u.lang AS user_lang "
        "FROM withdrawals w JOIN users u ON u.id = w.user_id "
        "WHERE w.status = 'pending' ORDER BY w.id ASC LIMIT ?",
        (limit,),
    )


async def get_withdrawal(withdrawal_id: int) -> dict | None:
    return await db.fetchone(
        "SELECT w.*, u.tg_id AS user_tg, u.full_name AS user_name, u.lang AS user_lang "
        "FROM withdrawals w JOIN users u ON u.id = w.user_id WHERE w.id = ?",
        (withdrawal_id,),
    )


async def approve_withdrawal(withdrawal_id: int, admin_tg: int) -> dict:
    async with db.transaction() as ex:
        withdrawal = await ex.fetchone(
            "SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)
        )
        if withdrawal is None:
            raise ServiceError("So'rov topilmadi")
        if withdrawal["status"] != "pending":
            raise ServiceError("Bu so'rov allaqachon ko'rib chiqilgan")
        await ex.execute(
            "UPDATE withdrawals SET status = 'paid', reviewed_by = ?, reviewed_at = ? "
            "WHERE id = ?",
            (admin_tg, utcnow_iso(), withdrawal_id),
        )
        user = await ex.fetchone(
            "SELECT tg_id, lang FROM users WHERE id = ?", (withdrawal["user_id"],)
        )
        return {
            "withdrawal_id": withdrawal_id,
            "amount": int(withdrawal["amount"]),
            "card": withdrawal["card_number"],
            "user_tg": user["tg_id"] if user else None,
            "user_lang": user["lang"] if user else None,
        }


async def reject_withdrawal(withdrawal_id: int, admin_tg: int, reason: str = "") -> dict:
    async with db.transaction() as ex:
        withdrawal = await ex.fetchone(
            "SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)
        )
        if withdrawal is None:
            raise ServiceError("So'rov topilmadi")
        if withdrawal["status"] != "pending":
            raise ServiceError("Bu so'rov allaqachon ko'rib chiqilgan")
        await ex.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (withdrawal["amount"], withdrawal["user_id"]),
        )
        await ex.execute(
            "UPDATE withdrawals SET status = 'rejected', reviewed_by = ?, reviewed_at = ? "
            "WHERE id = ?",
            (admin_tg, utcnow_iso(), withdrawal_id),
        )
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, 'refund', 'real', ?, ?, ?)",
            (
                withdrawal["user_id"],
                int(withdrawal["amount"]),
                f"withdrawal:{withdrawal_id}:refund",
                f"Pul yechish so'rovi #{withdrawal_id} bekor qilindi",
                utcnow_iso(),
            ),
        )
        user = await ex.fetchone(
            "SELECT tg_id, lang FROM users WHERE id = ?", (withdrawal["user_id"],)
        )
        return {
            "withdrawal_id": withdrawal_id,
            "amount": int(withdrawal["amount"]),
            "reason": reason,
            "user_tg": user["tg_id"] if user else None,
            "user_lang": user["lang"] if user else None,
        }
