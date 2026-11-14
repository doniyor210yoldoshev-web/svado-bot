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
    """Hamyon xulosasi: balans, kirim, chiqim, kutilayotgan yechish."""
    # Jami kirim (barcha topup tranzaksiyalari)
    total_in_row = await db.fetchone(
        "SELECT COALESCE(SUM(amount),0) AS total FROM transactions "
        "WHERE user_id = ? AND amount > 0 AND mode = 'real'",
        (user["id"],),
    )
    # Jami chiqim (barcha manfiy tranzaksiyalar)
    total_out_row = await db.fetchone(
        "SELECT COALESCE(SUM(-amount),0) AS total FROM transactions "
        "WHERE user_id = ? AND amount < 0 AND mode = 'real'",
        (user["id"],),
    )
    # Savdo daromadi
    earned = await db.fetchone(
        "SELECT COALESCE(SUM(amount),0) AS total FROM transactions "
        "WHERE user_id = ? AND type = 'sale' AND mode = 'real'",
        (user["id"],),
    )
    # Xarid xarajatlar
    spent = await db.fetchone(
        "SELECT COALESCE(SUM(-amount),0) AS total FROM transactions "
        "WHERE user_id = ? AND type = 'purchase' AND mode = 'real'",
        (user["id"],),
    )
    # Kutilayotgan yechish
    pending_out = await db.fetchone(
        "SELECT COALESCE(SUM(amount),0) AS total FROM withdrawals "
        "WHERE user_id = ? AND status = 'pending'",
        (user["id"],),
    )
    # Kutilayotgan to'ldirishlar (hali balansga qo'shilmagan)
    pending_in = await db.fetchone(
        "SELECT COALESCE(SUM(amount),0) AS total FROM topups "
        "WHERE user_id = ? AND status = 'pending'",
        (user["id"],),
    )
    # Tranzaksiyalar soni
    tx_count_row = await db.fetchone(
        "SELECT COUNT(*) AS cnt FROM transactions WHERE user_id = ? AND mode = 'real'",
        (user["id"],),
    )
    return {
        "balance": int(user["balance"]),
        "test_balance": int(user["test_balance"]),
        "test_mode": bool(user["test_mode"]),
        "total_in": int(total_in_row["total"]) if total_in_row else 0,
        "total_out": int(total_out_row["total"]) if total_out_row else 0,
        "earned": int(earned["total"]) if earned else 0,
        "spent": int(spent["total"]) if spent else 0,
        "pending_topups": int(pending_in["total"]) if pending_in else 0,
        "pending_withdrawals": int(pending_out["total"]) if pending_out else 0,
        "tx_count": int(tx_count_row["cnt"]) if tx_count_row else 0,
    }


async def list_transactions(user_id: int, mode: str = "real", limit: int = 20) -> list[dict]:
    """Foydalanuvchi tranzaksiyalar tarixi — eng yangilari birinchi."""
    return await db.fetchall(
        "SELECT * FROM transactions WHERE user_id = ? AND mode = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, mode, limit),
    )


async def get_transaction(tx_id: int, user_id: int) -> dict | None:
    """Bitta tranzaksiyani olish (foydalanuvchi o'ziga tegishligini tekshirib)."""
    return await db.fetchone(
        "SELECT * FROM transactions WHERE id = ? AND user_id = ?",
        (tx_id, user_id),
    )


async def get_wallet(user_id: int) -> dict | None:
    """Wallet row for a user (kept in sync with ``users.balance`` by triggers)."""
    return await db.fetchone(
        "SELECT w.*, u.tg_id, u.full_name, u.username "
        "FROM wallets w JOIN users u ON u.id = w.user_id WHERE w.user_id = ?",
        (user_id,),
    )


async def list_wallets(limit: int = 10, offset: int = 0) -> list[dict]:
    """Admin: barcha foydalanuvchilar hamyonlari (balans bo'yicha kamayish)."""
    return await db.fetchall(
        "SELECT w.*, u.tg_id, u.full_name, u.username, u.seller_code "
        "FROM wallets w JOIN users u ON u.id = w.user_id "
        "ORDER BY w.balance DESC, w.user_id ASC LIMIT ? OFFSET ?",
        (limit, max(0, offset)),
    )


async def count_wallets() -> int:
    row = await db.fetchone("SELECT COUNT(*) AS cnt FROM wallets")
    return int(row["cnt"]) if row else 0


async def list_all_transactions(limit: int = 10, offset: int = 0) -> list[dict]:
    """Admin: barcha tranzaksiyalar (foydalanuvchi ma'lumotlari bilan)."""
    return await db.fetchall(
        "SELECT t.*, u.tg_id AS user_tg, u.full_name AS user_name, "
        "u.username AS user_username "
        "FROM transactions t LEFT JOIN users u ON u.id = t.user_id "
        "ORDER BY t.id DESC LIMIT ? OFFSET ?",
        (limit, max(0, offset)),
    )


async def count_transactions() -> int:
    row = await db.fetchone("SELECT COUNT(*) AS cnt FROM transactions")
    return int(row["cnt"]) if row else 0


async def list_wallet_audit_logs(limit: int = 20) -> list[dict]:
    """Admin tomonidan balansga qilingan barcha aralashuvlar tarixi."""
    return await db.fetchall(
        "SELECT a.*, u.tg_id AS user_tg, u.full_name AS user_name "
        "FROM wallet_audit_logs a LEFT JOIN users u ON u.id = a.user_id "
        "ORDER BY a.id DESC LIMIT ?",
        (limit,),
    )


async def _wallet_audit(
    ex,
    user_id: int,
    actor_tg: int | None,
    action: str,
    amount: int,
    balance_before: int,
    balance_after: int,
    note: str,
    reference: str,
) -> None:
    await ex.execute(
        "INSERT INTO wallet_audit_logs "
        "(user_id, actor_tg, action, amount, balance_before, balance_after, "
        "note, reference, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            actor_tg,
            action,
            amount,
            balance_before,
            balance_after,
            (note or "")[:500],
            reference,
            utcnow_iso(),
        ),
    )


async def purchase_via_wallet(
    buyer_id: int, account_id: int, price: int, order_id: int, test_mode: bool = False
) -> dict:
    """Hamyon balansidan xarid qilish (atomic).

    Agar balansi yetarli bo'lmasa — InsufficientFunds xatosi qaytaradi.
    Muvaffaqiyatli bo'lsa — transaction yozuvi yaratiladi va order holati yangilanadi.
    """
    balance_field = "test_balance" if test_mode else "balance"
    tx_mode = "test" if test_mode else "real"

    async with db.transaction() as ex:
        user = await ex.fetchone("SELECT * FROM users WHERE id = ?", (buyer_id,))
        if user is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        current_balance = int(user[balance_field])
        if current_balance < price:
            raise InsufficientFunds(price, current_balance)

        # Atomik: balansni yechish
        updated = await ex.execute(
            f"UPDATE users SET {balance_field} = {balance_field} - ?, updated_at = ? "
            f"WHERE id = ? AND {balance_field} >= ?",
            (price, utcnow_iso(), buyer_id, price),
        )
        if updated == 0:
            raise InsufficientFunds(price, current_balance)

        # Tranzaksiya yozuvi
        ref = security.generate_reference("purchase")
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, order_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, ?, 'purchase', ?, ?, ?, ?)",
            (
                buyer_id,
                order_id,
                -price,
                tx_mode,
                ref,
                f"Akkaunt xaridi (buyurtma #{order_id})",
                utcnow_iso(),
            ),
        )

        updated_user = await ex.fetchone("SELECT * FROM users WHERE id = ?", (buyer_id,))
        return {
            "new_balance": int(updated_user[balance_field]) if updated_user else 0,
            "deducted": price,
            "reference": ref,
        }


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
    public_id = security.generate_payment_id()
    topup_id = await db.insert(
        "INSERT INTO topups "
        "(public_id, user_id, amount, receipt_type, receipt_value, status, mode, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', 'real', ?)",
        (public_id, user_id, amount, receipt_type, receipt_value.strip(), utcnow_iso()),
    )
    return await db.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))


async def create_click_topup(user_id: int, amount: int) -> dict:
    """Create a pending top-up that will be confirmed by Click's callback."""
    if amount < MIN_TOPUP or amount > MAX_TOPUP:
        raise ServiceError(
            f"To'ldirish miqdori {fmt_sum(MIN_TOPUP)} va {fmt_sum(MAX_TOPUP)} orasida bo'lishi kerak"
        )
    public_id = security.generate_payment_id()
    topup_id = await db.insert(
        "INSERT INTO topups "
        "(public_id, user_id, amount, receipt_type, receipt_value, status, mode, created_at) "
        "VALUES (?, ?, ?, 'click', '', 'pending', 'real', ?)",
        (public_id, user_id, amount, utcnow_iso()),
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


# --------------------------------------------------------------------------
# HamyonAPI top-ups
# --------------------------------------------------------------------------
async def unique_hamyon_amount(amount: int, exclude_order_id: int | None = None) -> int:
    """HamyonAPI allows only one open payment per amount per shop.

    When a collision happens the amount is nudged by 1 so'm (the official
    recommendation) until a free value is found. Open escrow orders are
    included because their payment uses the same shop.
    """
    candidate = int(amount)
    for _ in range(50):
        topup = await db.fetchone(
            "SELECT id FROM topups "
            "WHERE receipt_type = 'hamyon' AND status = 'pending' "
            "AND mode = 'real' AND amount = ?",
            (candidate,),
        )
        if topup is not None:
            candidate += 1
            continue
        order = await db.fetchone(
            "SELECT id FROM orders "
            "WHERE status = 'pending_payment' AND mode = 'real' "
            "AND COALESCE(payment_amount, price) = ? AND id <> ?",
            (candidate, exclude_order_id or 0),
        )
        if order is None:
            return candidate
        candidate += 1
    raise ServiceError(
        "Hozir juda ko'p to'lov kutilmoqda. Bir necha daqiqadan keyin urinib ko'ring."
    )


async def create_hamyon_topup(user_id: int, amount: int) -> dict:
    """Create a pending top-up that will be confirmed by HamyonAPI's callback.

    The amount may be nudged up by a few so'm when another open payment uses
    the same amount — HamyonAPI detects payments by amount.
    """
    if amount < MIN_TOPUP or amount > MAX_TOPUP:
        raise ServiceError(
            f"To'ldirish miqdori {fmt_sum(MIN_TOPUP)} va {fmt_sum(MAX_TOPUP)} orasida bo'lishi kerak"
        )
    unique_amount = await unique_hamyon_amount(amount)
    public_id = security.generate_payment_id()
    topup_id = await db.insert(
        "INSERT INTO topups "
        "(public_id, user_id, amount, receipt_type, receipt_value, status, mode, created_at) "
        "VALUES (?, ?, ?, 'hamyon', '', 'pending', 'real', ?)",
        (public_id, user_id, unique_amount, utcnow_iso()),
    )
    row = await db.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))
    if row is None:
        raise ServiceError("To'lov yaratib bo'lmadi. Keyinroq urinib ko'ring.")
    return row


async def get_hamyon_topup(payment_id: str, order_id: int | None = None) -> dict | None:
    """Find a HamyonAPI top-up by payment_id or our order_id (any status)."""
    base_query = (
        "SELECT t.*, u.tg_id AS user_tg, u.full_name AS user_name, u.lang AS user_lang "
        "FROM topups t JOIN users u ON u.id = t.user_id "
        "WHERE t.receipt_type = 'hamyon'"
    )
    if payment_id:
        row = await db.fetchone(
            base_query + " AND t.receipt_value = ?",
            (payment_id,),
        )
        if row is not None:
            return row
    if order_id is not None:
        row = await db.fetchone(
            base_query + " AND t.id = ?",
            (order_id,),
        )
        if row is not None:
            return row
    return None


async def mark_hamyon_created(topup_id: int, payment_id: str) -> None:
    await db.execute(
        "UPDATE topups SET receipt_value = ? WHERE id = ? AND status = 'pending'",
        ((payment_id or "")[:200], topup_id),
    )


async def cancel_hamyon_topup(topup_id: int, payment_id: str = "") -> None:
    await db.execute(
        "UPDATE topups SET status = 'rejected', reviewed_by = 0, reviewed_at = ?, "
        "receipt_value = ? WHERE id = ? AND status = 'pending'",
        (utcnow_iso(), (payment_id or "")[:200], topup_id),
    )


async def complete_hamyon_topup(topup_id: int, payment_id: str = "") -> dict | None:
    """Credit the user's wallet once. Returns None for unknown/foreign top-ups."""
    async with db.transaction() as ex:
        topup = await ex.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))
        if topup is None or topup["receipt_type"] != "hamyon":
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
                (payment_id or topup["receipt_value"] or "hamyon")[:200],
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
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, 'topup', 'real', ?, ?, ?)",
            (
                topup["user_id"],
                int(topup["amount"]),
                f"topup:{topup_id}",
                f"HamyonAPI to'lov #{topup_id} (payment_id: {payment_id})"[:500],
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
        "WHERE t.status = 'pending' AND t.receipt_type NOT IN ('click', 'hamyon') "
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
        now = utcnow_iso()
        # Conditional update: only one concurrent approval can win, so a
        # double-click can never credit the wallet twice.
        updated = await ex.execute(
            "UPDATE topups SET status = 'approved', reviewed_by = ?, reviewed_at = ?, "
            "updated_at = ? WHERE id = ? AND status = 'pending'",
            (admin_tg, now, now, topup_id),
        )
        if updated == 0:
            raise ServiceError("Bu to'lov allaqachon ko'rib chiqilgan")
        user = await ex.fetchone(
            "SELECT * FROM users WHERE id = ?", (topup["user_id"],)
        )
        if user is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        before = int(user["balance"])
        await ex.execute(
            "UPDATE users SET balance = balance + ?, updated_at = ? WHERE id = ?",
            (int(topup["amount"]), now, topup["user_id"]),
        )
        reference = f"topup:{topup_id}"
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, status, reference, description, created_at) "
            "VALUES (?, ?, 'topup', 'real', 'completed', ?, ?, ?)",
            (
                topup["user_id"],
                int(topup["amount"]),
                reference,
                f"Balans to'ldirildi (to'lov #{topup.get('public_id') or topup_id})",
                now,
            ),
        )
        await _wallet_audit(
            ex,
            topup["user_id"],
            admin_tg,
            "topup.approve",
            int(topup["amount"]),
            before,
            before + int(topup["amount"]),
            f"To'lov #{topup.get('public_id') or topup_id} tasdiqlandi",
            reference,
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
        now = utcnow_iso()
        updated = await ex.execute(
            "UPDATE topups SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, "
            "updated_at = ? WHERE id = ? AND status = 'pending'",
            (admin_tg, now, now, topup_id),
        )
        if updated == 0:
            raise ServiceError("Bu to'lov allaqachon ko'rib chiqilgan")
        user = await ex.fetchone(
            "SELECT tg_id, lang, balance FROM users WHERE id = ?", (topup["user_id"],)
        )
        if user is not None:
            await _wallet_audit(
                ex,
                topup["user_id"],
                admin_tg,
                "topup.reject",
                0,
                int(user["balance"]),
                int(user["balance"]),
                (reason or f"To'lov #{topup.get('public_id') or topup_id} rad etildi")[:500],
                f"topup:{topup_id}",
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
            "UPDATE users SET balance = balance - ?, updated_at = ? "
            "WHERE id = ? AND balance >= ?",
            (amount, utcnow_iso(), user_id, amount),
        )
        if debited == 0:
            raise InsufficientFunds(amount, int(user["balance"]))
        await ex.execute(
            "UPDATE seller_balances SET available_balance = "
            "CASE WHEN available_balance >= ? THEN available_balance - ? ELSE 0 END, "
            "updated_at = ? WHERE seller_id = ?",
            (amount, amount, utcnow_iso(), user_id),
        )
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
    user_id: int,
    amount: int,
    note: str,
    admin_tg: int,
    tx_type: str = "adjustment",
) -> tuple[dict, int]:
    """Atomically credit/debit a wallet and write an audit trail.

    A conditional ``UPDATE`` guarantees the balance can never go negative, even
    when two admins act at the same time. Every manual change is stored both as
    a transaction and in ``wallet_audit_logs``.
    """
    if amount == 0:
        raise ServiceError("Summa nolga teng bo'lishi mumkin emas")
    if tx_type not in ("adjustment", "refund"):
        raise ServiceError("Noto'g'ri tranzaksiya turi")
    async with db.transaction() as ex:
        user = await ex.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        if user is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        before = int(user["balance"])
        now = utcnow_iso()
        updated_count = await ex.execute(
            "UPDATE users SET balance = balance + ?, updated_at = ? "
            "WHERE id = ? AND balance + ? >= 0",
            (amount, now, user_id, amount),
        )
        if updated_count == 0:
            raise ServiceError("Balans manfiy bo'lib qoladi")
        new_balance = before + amount
        reference = security.generate_reference(tx_type)
        description = (note or f"Admin {admin_tg} tomonidan tuzatish")[:500]
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, status, reference, description, created_at) "
            "VALUES (?, ?, ?, 'real', 'completed', ?, ?, ?)",
            (user_id, amount, tx_type, reference, description, now),
        )
        await ex.execute(
            "UPDATE seller_balances SET available_balance = "
            "CASE WHEN available_balance + ? >= 0 THEN available_balance + ? ELSE 0 END, "
            "updated_at = ? WHERE seller_id = ?",
            (amount, amount, now, user_id),
        )
        await _wallet_audit(
            ex,
            user_id,
            admin_tg,
            f"wallet.{tx_type}",
            amount,
            before,
            new_balance,
            description,
            reference,
        )
        updated = await ex.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        if updated is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        return updated, new_balance


async def admin_refund_wallet(
    user_id: int, amount: int, note: str, admin_tg: int
) -> tuple[dict, int]:
    """Admin tomonidan foydalanuvchi hamyoniga pul qaytarish."""
    if amount <= 0:
        raise ServiceError("Qaytarish summasi musbat bo'lishi kerak")
    return await admin_adjust_balance(
        user_id, amount, note or "Admin tomonidan qaytarish", admin_tg, tx_type="refund"
    )


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
        now = utcnow_iso()
        updated = await ex.execute(
            "UPDATE withdrawals SET status = 'paid', reviewed_by = ?, reviewed_at = ?, "
            "updated_at = ? WHERE id = ? AND status = 'pending'",
            (admin_tg, now, now, withdrawal_id),
        )
        if updated == 0:
            raise ServiceError("Bu so'rov allaqachon ko'rib chiqilgan")
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
        now = utcnow_iso()
        updated = await ex.execute(
            "UPDATE withdrawals SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, "
            "updated_at = ? WHERE id = ? AND status = 'pending'",
            (admin_tg, now, now, withdrawal_id),
        )
        if updated == 0:
            raise ServiceError("Bu so'rov allaqachon ko'rib chiqilgan")
        user = await ex.fetchone(
            "SELECT * FROM users WHERE id = ?", (withdrawal["user_id"],)
        )
        before = int(user["balance"]) if user else 0
        await ex.execute(
            "UPDATE users SET balance = balance + ?, updated_at = ? WHERE id = ?",
            (withdrawal["amount"], now, withdrawal["user_id"]),
        )
        await ex.execute(
            "UPDATE seller_balances SET available_balance = available_balance + ?, "
            "updated_at = ? WHERE seller_id = ?",
            (withdrawal["amount"], now, withdrawal["user_id"]),
        )
        reference = f"withdrawal:{withdrawal_id}:refund"
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, amount, type, mode, status, reference, description, created_at) "
            "VALUES (?, ?, 'refund', 'real', 'completed', ?, ?, ?)",
            (
                withdrawal["user_id"],
                int(withdrawal["amount"]),
                reference,
                f"Pul yechish so'rovi #{withdrawal_id} bekor qilindi",
                now,
            ),
        )
        await _wallet_audit(
            ex,
            withdrawal["user_id"],
            admin_tg,
            "withdrawal.reject",
            int(withdrawal["amount"]),
            before,
            before + int(withdrawal["amount"]),
            f"Pul yechish so'rovi #{withdrawal_id} rad etildi",
            reference,
        )
        return {
            "withdrawal_id": withdrawal_id,
            "amount": int(withdrawal["amount"]),
            "reason": reason,
            "user_tg": user["tg_id"] if user else None,
            "user_lang": user["lang"] if user else None,
        }
