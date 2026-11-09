"""Marketplace domain logic: accounts, orders, disputes and moderation."""

from __future__ import annotations

import logging
import math
from datetime import timedelta

from config import (
    AUTO_COMPLETE_MINUTES,
    CHECK_MINUTES,
    COMMISSION_PERCENT,
    MIN_PRICE,
    PAGE_SIZE,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
    SELLER_SHARE_PERCENT,
)
from database.db import db
from services import security
from services.errors import InsufficientFunds, ServiceError
from utils import fmt_sum, parse_dt, utcnow, utcnow_iso

logger = logging.getLogger(__name__)


def _balance_field(test: bool) -> str:
    return "test_balance" if test else "balance"


def _is_test(user: dict) -> bool:
    return bool(user.get("test_mode"))


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------
async def create_account(
    seller_id: int,
    title: str,
    description: str,
    login: str,
    password: str,
    price: int,
    mode: str = "real",
) -> int:
    title = (title or "").strip()
    description = (description or "").strip()
    login = (login or "").strip()
    if not 2 <= len(title) <= 100:
        raise ServiceError("Nom 2-100 belgi bo'lishi kerak")
    if len(description) > 1000:
        raise ServiceError("Tavsif 1000 belgidan oshmasligi kerak")
    if not 2 <= len(login) <= 200:
        raise ServiceError("Login 2-200 belgi bo'lishi kerak")
    if not 4 <= len(password) <= 200:
        raise ServiceError("Parol 4-200 belgi bo'lishi kerak")
    if price < MIN_PRICE:
        raise ServiceError(f"Narx kamida {fmt_sum(MIN_PRICE)} bo'lishi kerak")

    hashed = security.login_hash(login)
    exists = await db.fetchone("SELECT 1 AS found FROM accounts WHERE login_hash = ?", (hashed,))
    if exists:
        raise ServiceError("Bu login bilan akkaunt allaqachon qo'shilgan")

    return await db.insert(
        "INSERT INTO accounts "
        "(seller_id, title, description, login_enc, login_hash, password_enc, "
        "price, status, mode, is_approved, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?)",
        (
            seller_id,
            title,
            description,
            security.encrypt(login),
            hashed,
            security.encrypt(password),
            price,
            mode,
            utcnow_iso(),
        ),
    )


async def get_account(account_id: int) -> dict | None:
    return await db.fetchone("SELECT * FROM accounts WHERE id = ?", (account_id,))


async def get_account_login(account_id: int) -> str:
    account = await get_account(account_id)
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    return security.decrypt(account["login_enc"])


async def list_seller_accounts(seller_id: int, include_sold: bool = True) -> list[dict]:
    if include_sold:
        return await db.fetchall(
            "SELECT * FROM accounts WHERE seller_id = ? "
            "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 "
            "WHEN 'sold' THEN 2 ELSE 3 END, id DESC",
            (seller_id,),
        )
    return await db.fetchall(
        "SELECT * FROM accounts WHERE seller_id = ? AND status = 'active' ORDER BY id DESC",
        (seller_id,),
    )


async def delete_account(account_id: int, seller_id: int) -> None:
    account = await get_account(account_id)
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    if account["seller_id"] != seller_id:
        raise ServiceError("Bu akkaunt sizga tegishli emas")
    if account["status"] not in ("active", "paused"):
        raise ServiceError("Faqat sotuvdagi yoki to'xtatilgan akkauntni o'chirish mumkin")
    active = await db.fetchone(
        "SELECT 1 AS found FROM orders WHERE account_id = ? "
        "AND status IN ('checking','disputed')",
        (account_id,),
    )
    if active:
        raise ServiceError("Bu akkaunt bo'yicha faol buyurtma mavjud")
    await db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


async def set_account_paused(account_id: int, seller_id: int, paused: bool) -> None:
    account = await get_account(account_id)
    if account is None or account["seller_id"] != seller_id:
        raise ServiceError("Akkaunt topilmadi")
    if account["status"] not in ("active", "paused"):
        raise ServiceError("Bu akkaunt holatini o'zgartirib bo'lmaydi")
    active = await db.fetchone(
        "SELECT 1 AS found FROM orders WHERE account_id = ? "
        "AND status IN ('checking','disputed')",
        (account_id,),
    )
    if active:
        raise ServiceError("Faol buyurtma mavjud, avval uni yakunlang")
    await db.execute(
        "UPDATE accounts SET status = ? WHERE id = ?",
        ("paused" if paused else "active", account_id),
    )


async def update_account_price(account_id: int, seller_id: int, price: int) -> None:
    if price < MIN_PRICE:
        raise ServiceError(f"Narx kamida {fmt_sum(MIN_PRICE)} bo'lishi kerak")
    account = await get_account(account_id)
    if account is None or account["seller_id"] != seller_id:
        raise ServiceError("Akkaunt topilmadi")
    if account["status"] not in ("active", "paused"):
        raise ServiceError("Sotilgan akkaunt narxini o'zgartirib bo'lmaydi")
    await db.execute("UPDATE accounts SET price = ? WHERE id = ?", (price, account_id))


async def count_active_accounts(seller_id: int, mode: str = "real") -> int:
    row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM accounts a JOIN users u ON u.id = a.seller_id "
        "WHERE a.seller_id = ? AND a.status = 'active' AND a.mode = ? "
        "AND a.is_approved = 1 AND u.is_blocked = 0",
        (seller_id, mode),
    )
    return int(row["c"]) if row else 0


async def list_market_accounts(mode: str, page: int = 1) -> tuple[list[dict], int]:
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE
    total_row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM accounts a JOIN users u ON u.id = a.seller_id "
        "WHERE a.status = 'active' AND a.mode = ? AND a.is_approved = 1 "
        "AND u.is_blocked = 0",
        (mode,),
    )
    total = int(total_row["c"]) if total_row else 0
    accounts = await db.fetchall(
        "SELECT a.*, u.full_name AS seller_name, u.seller_code AS seller_code, "
        "u.is_verified AS seller_verified, u.rating AS seller_rating "
        "FROM accounts a JOIN users u ON u.id = a.seller_id "
        "WHERE a.status = 'active' AND a.mode = ? AND a.is_approved = 1 "
        "AND u.is_blocked = 0 ORDER BY a.id DESC LIMIT ? OFFSET ?",
        (mode, PAGE_SIZE, offset),
    )
    return accounts, total


async def list_active_accounts(seller_id: int, mode: str = "real", limit: int = 40) -> list[dict]:
    return await db.fetchall(
        "SELECT a.* FROM accounts a JOIN users u ON u.id = a.seller_id "
        "WHERE a.seller_id = ? AND a.status = 'active' AND a.mode = ? "
        "AND a.is_approved = 1 AND u.is_blocked = 0 ORDER BY a.id DESC LIMIT ?",
        (seller_id, mode, limit),
    )


async def admin_list_accounts(limit: int = 30) -> list[dict]:
    return await db.fetchall(
        "SELECT a.*, u.full_name AS seller_name, u.seller_code AS seller_code "
        "FROM accounts a JOIN users u ON u.id = a.seller_id "
        "ORDER BY a.id DESC LIMIT ?",
        (limit,),
    )


async def admin_set_account_status(account_id: int, status: str) -> None:
    if status not in ("active", "paused"):
        raise ServiceError("Noto'g'ri holat")
    await db.execute("UPDATE accounts SET status = ? WHERE id = ?", (status, account_id))


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
async def create_order(buyer_id: int, account_id: int) -> tuple[dict, dict, dict]:
    now = utcnow()
    check_deadline = (now + timedelta(minutes=CHECK_MINUTES)).isoformat(timespec="seconds")
    auto_complete_at = (
        now + timedelta(minutes=CHECK_MINUTES + AUTO_COMPLETE_MINUTES)
    ).isoformat(timespec="seconds")

    async with db.transaction() as ex:
        buyer = await ex.fetchone("SELECT * FROM users WHERE id = ?", (buyer_id,))
        if buyer is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        if buyer["is_blocked"]:
            raise ServiceError("Hisobingiz bloklangan")

        test = _is_test(buyer)
        mode = "test" if test else "real"
        balance_field = _balance_field(test)

        account = await ex.fetchone("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if account is None:
            raise ServiceError("Akkaunt topilmadi")
        if account["status"] != "active":
            raise ServiceError("Bu akkaunt allaqachon sotilgan")
        if account["mode"] != mode:
            raise ServiceError("Bu akkaunt sizning rejimingizda mavjud emas")
        if account["seller_id"] == buyer_id:
            raise ServiceError("O'z akkauntingizni sotib ola olmaysiz")

        seller = await ex.fetchone(
            "SELECT * FROM users WHERE id = ?", (account["seller_id"],)
        )
        if seller is None or seller["is_blocked"]:
            raise ServiceError("Sotuvchi faol emas")

        if int(buyer[balance_field]) < int(account["price"]):
            raise InsufficientFunds(int(account["price"]), int(buyer[balance_field]))

        claimed = await ex.execute(
            "UPDATE accounts SET status = 'sold', sold_at = ? "
            "WHERE id = ? AND status = 'active'",
            (utcnow_iso(), account_id),
        )
        if claimed == 0:
            raise ServiceError("Bu akkaunt allaqachon sotilgan")

        debited = await ex.execute(
            f"UPDATE users SET {balance_field} = {balance_field} - ? "
            f"WHERE id = ? AND {balance_field} >= ?",
            (account["price"], buyer_id, account["price"]),
        )
        if debited == 0:
            raise InsufficientFunds(int(account["price"]), int(buyer[balance_field]))

        order_id = await ex.insert(
            "INSERT INTO orders "
            "(account_id, seller_id, buyer_id, price, status, mode, check_deadline, "
            "auto_complete_at, created_at) VALUES (?, ?, ?, ?, 'checking', ?, ?, ?, ?)",
            (
                account_id,
                account["seller_id"],
                buyer_id,
                account["price"],
                mode,
                check_deadline,
                auto_complete_at,
                utcnow_iso(),
            ),
        )
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, order_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, ?, 'purchase', ?, ?, ?, ?)",
            (
                buyer_id,
                order_id,
                -int(account["price"]),
                mode,
                f"order:{order_id}:purchase",
                f"Akkaunt #{account_id} xaridi uchun escrow",
                utcnow_iso(),
            ),
        )
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma yaratib bo'lmadi")
    return order, account, seller


async def get_order(order_id: int) -> dict | None:
    return await db.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))


async def get_order_with_people(order_id: int) -> dict | None:
    return await db.fetchone(
        "SELECT o.*, b.tg_id AS buyer_tg, b.full_name AS buyer_name, "
        "s.tg_id AS seller_tg, s.full_name AS seller_name, s.seller_code AS seller_code "
        "FROM orders o JOIN users b ON b.id = o.buyer_id "
        "JOIN users s ON s.id = o.seller_id WHERE o.id = ?",
        (order_id,),
    )


async def get_credentials(order_id: int, buyer_id: int) -> tuple[str, str]:
    order = await get_order(order_id)
    if order is None:
        raise ServiceError("Buyurtma topilmadi")
    if order["buyer_id"] != buyer_id:
        raise ServiceError("Bu buyurtma sizga tegishli emas")
    if order["status"] not in ("checking", "completed", "auto_completed", "disputed"):
        raise ServiceError("Bu buyurtma bo'yicha login-parol ko'rsatilmaydi")
    account = await get_account(order["account_id"])
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    try:
        return security.decrypt(account["login_enc"]), security.decrypt(account["password_enc"])
    except ValueError as exc:
        raise ServiceError("Login-parolni ochishda xatolik") from exc


async def get_credentials_admin(order_id: int) -> tuple[str, str]:
    order = await get_order(order_id)
    if order is None:
        raise ServiceError("Buyurtma topilmadi")
    account = await get_account(order["account_id"])
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    try:
        return security.decrypt(account["login_enc"]), security.decrypt(account["password_enc"])
    except ValueError as exc:
        raise ServiceError("Login-parolni ochishda xatolik") from exc


async def _settle_order(ex, order: dict, new_status: str) -> dict:
    total = int(order["price"]) + int(order["penalty"])
    seller_share = total * SELLER_SHARE_PERCENT // 100
    commission = total - seller_share
    now = utcnow_iso()
    test = order["mode"] == "test"
    balance_field = _balance_field(test)

    await ex.execute(
        f"UPDATE users SET {balance_field} = {balance_field} + ? WHERE id = ?",
        (seller_share, order["seller_id"]),
    )
    await ex.execute(
        "UPDATE orders SET status = ?, completed_at = ? WHERE id = ?",
        (new_status, now, order["id"]),
    )
    await ex.execute(
        "UPDATE accounts SET status = 'sold', sold_at = COALESCE(sold_at, ?) WHERE id = ?",
        (now, order["account_id"]),
    )
    await ex.execute(
        "INSERT INTO transactions "
        "(user_id, order_id, amount, type, mode, reference, description, created_at) "
        "VALUES (?, ?, ?, 'sale', ?, ?, ?, ?)",
        (
            order["seller_id"],
            order["id"],
            seller_share,
            order["mode"],
            f"order:{order['id']}:sale",
            f"Savdo #{order['id']} daromadi ({SELLER_SHARE_PERCENT}%)",
            now,
        ),
    )
    await ex.execute(
        "INSERT INTO transactions "
        "(user_id, order_id, amount, type, mode, reference, description, created_at) "
        "VALUES (NULL, ?, ?, 'commission', ?, ?, ?, ?)",
        (
            order["id"],
            commission,
            order["mode"],
            f"order:{order['id']}:commission",
            f"Savdo #{order['id']} komissiyasi ({COMMISSION_PERCENT}%)",
            now,
        ),
    )
    buyer = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["buyer_id"],))
    seller = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["seller_id"],))
    return {
        "order_id": order["id"],
        "status": new_status,
        "price": int(order["price"]),
        "penalty": int(order["penalty"]),
        "total": total,
        "seller_share": seller_share,
        "commission": commission,
        "mode": order["mode"],
        "buyer_tg": buyer["tg_id"] if buyer else None,
        "buyer_lang": buyer["lang"] if buyer else None,
        "seller_tg": seller["tg_id"] if seller else None,
        "seller_lang": seller["lang"] if seller else None,
    }


async def confirm_order(order_id: int, buyer_id: int) -> dict:
    async with db.transaction() as ex:
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma topilmadi")
        if order["buyer_id"] != buyer_id:
            raise ServiceError("Bu buyurtma sizga tegishli emas")
        if order["status"] == "disputed":
            raise ServiceError("Shikoyat ko'rib chiqilmoqda. Admin javobini kuting")
        if order["status"] != "checking":
            raise ServiceError("Bu buyurtma allaqachon yakunlangan")
        return await _settle_order(ex, order, "completed")


async def create_dispute(order_id: int, buyer_id: int, reason: str) -> dict:
    reason = (reason or "").strip()
    if not 5 <= len(reason) <= 1000:
        raise ServiceError("Shikoyat sababi 5 dan 1000 belgigacha bo'lishi kerak")
    async with db.transaction() as ex:
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma topilmadi")
        if order["buyer_id"] != buyer_id:
            raise ServiceError("Bu buyurtma sizga tegishli emas")
        if order["status"] != "checking":
            raise ServiceError("Bu buyurtma bo'yicha shikoyat ochib bo'lmaydi")
        await ex.insert(
            "INSERT INTO disputes (order_id, buyer_id, reason, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (order_id, buyer_id, reason, utcnow_iso()),
        )
        await ex.execute("UPDATE orders SET status = 'disputed' WHERE id = ?", (order_id,))
        updated = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if updated is None:
            raise ServiceError("Buyurtma topilmadi")
        return updated


async def accrue_penalties() -> list[dict]:
    events: list[dict] = []
    rows = await db.fetchall("SELECT id FROM orders WHERE status = 'checking'")
    interval_seconds = PENALTY_INTERVAL_MINUTES * 60

    for row in rows:
        async with db.transaction() as ex:
            order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (row["id"],))
            if order is None or order["status"] != "checking":
                continue

            now = utcnow()
            deadline = parse_dt(order["check_deadline"])
            auto_at = parse_dt(order["auto_complete_at"])
            if deadline is None or auto_at is None:
                continue

            buyer = await ex.fetchone(
                "SELECT tg_id, lang FROM users WHERE id = ?", (order["buyer_id"],)
            )
            buyer_tg = buyer["tg_id"] if buyer else None
            buyer_lang = buyer["lang"] if buyer else None

            if now < deadline and buyer_tg is not None:
                remaining = (deadline - now).total_seconds()
                if remaining <= 60 and not order["reminder_1_sent"]:
                    await ex.execute(
                        "UPDATE orders SET reminder_1_sent = 1, reminder_5_sent = 1 "
                        "WHERE id = ?",
                        (order["id"],),
                    )
                    events.append(
                        {
                            "tg": buyer_tg,
                            "lang": buyer_lang,
                            "key": "notify.reminder_1",
                            "kwargs": {"order": order["id"]},
                            "order_id": order["id"],
                        }
                    )
                elif remaining <= 300 and not order["reminder_5_sent"]:
                    await ex.execute(
                        "UPDATE orders SET reminder_5_sent = 1 WHERE id = ?", (order["id"],)
                    )
                    events.append(
                        {
                            "tg": buyer_tg,
                            "lang": buyer_lang,
                            "key": "notify.reminder_5",
                            "kwargs": {"order": order["id"]},
                            "order_id": order["id"],
                        }
                    )

            if now > deadline:
                delay = (now - deadline).total_seconds()
                ticks_due = max(1, math.ceil(delay / interval_seconds))
                new_ticks = ticks_due - int(order["penalty_ticks"])
                if new_ticks > 0:
                    penalty_each = max(1, round(int(order["price"]) * PENALTY_PERCENT / 100))
                    extra = penalty_each * new_ticks
                    test = order["mode"] == "test"
                    balance_field = _balance_field(test)
                    await ex.execute(
                        f"UPDATE users SET {balance_field} = {balance_field} - ? WHERE id = ?",
                        (extra, order["buyer_id"]),
                    )
                    await ex.execute(
                        "UPDATE orders SET penalty = penalty + ?, penalty_ticks = ?, "
                        "last_penalty_at = ? WHERE id = ?",
                        (extra, ticks_due, utcnow_iso(), order["id"]),
                    )
                    await ex.execute(
                        "INSERT INTO transactions "
                        "(user_id, order_id, amount, type, mode, reference, description, created_at) "
                        "VALUES (?, ?, ?, 'penalty', ?, ?, ?, ?)",
                        (
                            order["buyer_id"],
                            order["id"],
                            -extra,
                            order["mode"],
                            f"order:{order['id']}:penalty:{ticks_due}",
                            f"Buyurtma #{order['id']} kechikish jarimasi ({PENALTY_PERCENT}% x {new_ticks})",
                            utcnow_iso(),
                        ),
                    )
                    order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order["id"],))
                    if buyer_tg is not None and order is not None:
                        total = int(order["price"]) + int(order["penalty"])
                        events.append(
                            {
                                "tg": buyer_tg,
                                "lang": buyer_lang,
                                "key": "notify.penalty",
                                "kwargs": {
                                    "order": order["id"],
                                    "interval": PENALTY_INTERVAL_MINUTES,
                                    "percent": PENALTY_PERCENT,
                                    "extra": fmt_sum(extra),
                                    "penalty": fmt_sum(order["penalty"]),
                                    "total": fmt_sum(total),
                                },
                                "order_id": order["id"],
                            }
                        )

            if now >= auto_at:
                order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order["id"],))
                if order is not None and order["status"] == "checking":
                    info = await _settle_order(ex, order, "auto_completed")
                    if info["buyer_tg"]:
                        events.append(
                            {
                                "tg": info["buyer_tg"],
                                "lang": info["buyer_lang"],
                                "key": "notify.buyer_auto",
                                "kwargs": {
                                    "order": info["order_id"],
                                    "penalty": fmt_sum(info["penalty"]),
                                    "total": fmt_sum(info["total"]),
                                },
                                "order_id": None,
                            }
                        )
                    if info["seller_tg"]:
                        events.append(
                            {
                                "tg": info["seller_tg"],
                                "lang": info["seller_lang"],
                                "key": "notify.seller_auto",
                                "kwargs": {
                                    "order": info["order_id"],
                                    "share": fmt_sum(info["seller_share"]),
                                    "percent": SELLER_SHARE_PERCENT,
                                },
                                "order_id": None,
                            }
                        )
    return events


async def resolve_dispute(order_id: int, action: str, admin_tg_id: int) -> dict:
    if action not in ("refund", "release"):
        raise ServiceError("Noto'g'ri amal")
    async with db.transaction() as ex:
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        dispute = await ex.fetchone(
            "SELECT * FROM disputes WHERE order_id = ?", (order_id,)
        )
        if order is None or dispute is None:
            raise ServiceError("Shikoyat topilmadi")
        if dispute["status"] != "pending":
            raise ServiceError("Bu shikoyat allaqachon hal qilingan")
        if order["status"] != "disputed":
            raise ServiceError("Buyurtma shikoyat holatida emas")

        now = utcnow_iso()
        if action == "refund":
            total = int(order["price"]) + int(order["penalty"])
            test = order["mode"] == "test"
            balance_field = _balance_field(test)
            await ex.execute(
                f"UPDATE users SET {balance_field} = {balance_field} + ? WHERE id = ?",
                (total, order["buyer_id"]),
            )
            await ex.execute(
                "UPDATE orders SET status = 'refunded', completed_at = ? WHERE id = ?",
                (now, order_id),
            )
            await ex.execute(
                "UPDATE accounts SET status = 'active', sold_at = NULL WHERE id = ?",
                (order["account_id"],),
            )
            await ex.execute(
                "INSERT INTO transactions "
                "(user_id, order_id, amount, type, mode, reference, description, created_at) "
                "VALUES (?, ?, ?, 'refund', ?, ?, ?, ?)",
                (
                    order["buyer_id"],
                    order_id,
                    total,
                    order["mode"],
                    f"order:{order_id}:refund",
                    f"Buyurtma #{order_id} uchun pul qaytarildi",
                    now,
                ),
            )
            await ex.execute(
                "UPDATE disputes SET status = 'refunded', resolved_by = ?, resolved_at = ? "
                "WHERE order_id = ?",
                (admin_tg_id, now, order_id),
            )
            buyer = await ex.fetchone(
                "SELECT tg_id, lang FROM users WHERE id = ?", (order["buyer_id"],)
            )
            seller = await ex.fetchone(
                "SELECT tg_id, lang FROM users WHERE id = ?", (order["seller_id"],)
            )
            return {
                "action": "refund",
                "order_id": order_id,
                "total": total,
                "buyer_tg": buyer["tg_id"] if buyer else None,
                "buyer_lang": buyer["lang"] if buyer else None,
                "seller_tg": seller["tg_id"] if seller else None,
                "seller_lang": seller["lang"] if seller else None,
            }

        info = await _settle_order(ex, order, "completed")
        await ex.execute(
            "UPDATE disputes SET status = 'released', resolved_by = ?, resolved_at = ? "
            "WHERE order_id = ?",
            (admin_tg_id, now, order_id),
        )
        info["action"] = "release"
        return info


async def list_buyer_orders(buyer_id: int, limit: int = 20) -> list[dict]:
    return await db.fetchall(
        "SELECT o.*, a.title AS title, a.price AS account_price, "
        "s.full_name AS seller_name, s.seller_code AS seller_code "
        "FROM orders o JOIN accounts a ON a.id = o.account_id "
        "JOIN users s ON s.id = o.seller_id WHERE o.buyer_id = ? "
        "ORDER BY o.id DESC LIMIT ?",
        (buyer_id, limit),
    )


async def list_seller_sales(seller_id: int, limit: int = 20) -> list[dict]:
    return await db.fetchall(
        "SELECT o.*, a.title AS title, b.full_name AS buyer_name, b.tg_id AS buyer_tg "
        "FROM orders o JOIN accounts a ON a.id = o.account_id "
        "JOIN users b ON b.id = o.buyer_id WHERE o.seller_id = ? "
        "ORDER BY o.id DESC LIMIT ?",
        (seller_id, limit),
    )


async def list_recent_sales(limit: int = 20) -> list[dict]:
    return await db.fetchall(
        "SELECT o.*, a.title AS title, b.full_name AS buyer_name, "
        "s.full_name AS seller_name, s.seller_code AS seller_code "
        "FROM orders o JOIN accounts a ON a.id = o.account_id "
        "JOIN users b ON b.id = o.buyer_id JOIN users s ON s.id = o.seller_id "
        "WHERE o.status IN ('completed','auto_completed','refunded') "
        "ORDER BY o.id DESC LIMIT ?",
        (limit,),
    )


# --------------------------------------------------------------------------
# Disputes
# --------------------------------------------------------------------------
async def get_dispute_details(order_id: int) -> dict | None:
    return await db.fetchone(
        "SELECT d.*, o.price, o.penalty, o.status AS order_status, o.account_id, "
        "b.tg_id AS buyer_tg, b.full_name AS buyer_name, b.lang AS buyer_lang, "
        "s.tg_id AS seller_tg, s.full_name AS seller_name, s.seller_code, "
        "s.lang AS seller_lang "
        "FROM disputes d JOIN orders o ON o.id = d.order_id "
        "JOIN users b ON b.id = d.buyer_id JOIN users s ON s.id = o.seller_id "
        "WHERE d.order_id = ?",
        (order_id,),
    )


async def list_pending_disputes(limit: int = 10) -> list[dict]:
    return await db.fetchall(
        "SELECT d.*, o.price, o.penalty, o.status AS order_status, o.account_id, "
        "b.tg_id AS buyer_tg, b.full_name AS buyer_name, "
        "s.tg_id AS seller_tg, s.full_name AS seller_name, s.seller_code "
        "FROM disputes d JOIN orders o ON o.id = d.order_id "
        "JOIN users b ON b.id = d.buyer_id JOIN users s ON s.id = o.seller_id "
        "WHERE d.status = 'pending' ORDER BY d.id ASC LIMIT ?",
        (limit,),
    )


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
async def search_by_id(query: str, buyer_mode: str) -> dict | None:
    query = (query or "").strip()
    if not query.isdigit():
        return None
    seller = await db.fetchone(
        "SELECT * FROM users WHERE seller_code = ? AND is_seller = 1 AND is_blocked = 0",
        (query,),
    )
    if seller is not None:
        return {"type": "seller", "seller": seller}
    account = await get_account(int(query))
    if account is not None and account["mode"] == buyer_mode:
        return {"type": "account", "account": account}
    return None


# --------------------------------------------------------------------------
# Admin statistics and audit
# --------------------------------------------------------------------------
async def admin_stats() -> dict[str, int]:
    async def scalar(sql: str, params: tuple = ()) -> int:
        row = await db.fetchone(sql, params)
        return int(row["c"]) if row and row["c"] is not None else 0

    return {
        "users": await scalar("SELECT COUNT(*) AS c FROM users"),
        "sellers": await scalar("SELECT COUNT(*) AS c FROM users WHERE is_seller = 1"),
        "active": await scalar(
            "SELECT COUNT(*) AS c FROM accounts WHERE status = 'active' AND mode = 'real'"
        ),
        "sold": await scalar(
            "SELECT COUNT(*) AS c FROM accounts WHERE status = 'sold' AND mode = 'real'"
        ),
        "checking": await scalar(
            "SELECT COUNT(*) AS c FROM orders WHERE status = 'checking'"
        ),
        "completed": await scalar(
            "SELECT COUNT(*) AS c FROM orders "
            "WHERE status IN ('completed','auto_completed') AND mode = 'real'"
        ),
        "disputed": await scalar(
            "SELECT COUNT(*) AS c FROM disputes WHERE status = 'pending'"
        ),
        "gmv": await scalar(
            "SELECT COALESCE(SUM(price + penalty),0) AS c FROM orders "
            "WHERE status IN ('completed','auto_completed') AND mode = 'real'"
        ),
        "commission": await scalar(
            "SELECT COALESCE(SUM(amount),0) AS c FROM transactions "
            "WHERE type = 'commission' AND mode = 'real'"
        ),
        "topups": await scalar(
            "SELECT COUNT(*) AS c FROM topups WHERE status = 'pending'"
        ),
        "withdrawals": await scalar(
            "SELECT COUNT(*) AS c FROM withdrawals WHERE status = 'pending'"
        ),
    }


async def log_audit(actor_tg: int | None, action: str, details: str = "") -> None:
    await db.execute(
        "INSERT INTO audit_logs (actor_tg, action, details, created_at) "
        "VALUES (?, ?, ?, ?)",
        (actor_tg, action, (details or "")[:1000], utcnow_iso()),
    )


async def list_audit_logs(limit: int = 20) -> list[dict]:
    return await db.fetchall(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
    )
