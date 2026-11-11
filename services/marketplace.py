"""Marketplace domain logic: accounts, orders, disputes and moderation."""

from __future__ import annotations

import logging
import math
import secrets
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
    TEST_MAX_ACCOUNTS,
)
from database.db import db
from services import security
from services import users as users_service
from services.errors import InsufficientFunds, ServiceError
from utils import fmt_sum, parse_dt, utcnow, utcnow_iso

logger = logging.getLogger(__name__)


def _balance_field(test: bool) -> str:
    return "test_balance" if test else "balance"


def _is_test(user: dict) -> bool:
    return bool(user.get("test_mode"))


# --------------------------------------------------------------------------
# Accounts
async def _next_display_id() -> int:
    row = await db.fetchone("SELECT MAX(COALESCE(display_id, 10000)) AS m FROM accounts")
    current_max = int(row["m"]) if row and row.get("m") else 10000
    return max(10001, current_max + 1)


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
    photo_id: str | None = None,
    extra_info: str = "",
    verification_code: str | None = None,
    verification_status: str = "verified",
    display_id: int | None = None,
) -> int:
    title = (title or "").strip()
    description = (description or "").strip()
    login = (login or "").strip()
    extra_info = (extra_info or "").strip()
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

    if display_id is None:
        display_id = await _next_display_id()

    is_approved = 1 if verification_status == "verified" else 0

    return await db.insert(
        "INSERT INTO accounts "
        "(display_id, seller_id, title, description, photo_id, extra_info, "
        "verification_code, verification_status, login_enc, login_hash, password_enc, "
        "price, status, mode, is_approved, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (
            display_id,
            seller_id,
            title,
            description,
            photo_id,
            extra_info,
            verification_code,
            verification_status,
            security.encrypt(login),
            hashed,
            security.encrypt(password),
            price,
            mode,
            is_approved,
            utcnow_iso(),
        ),
    )


async def verify_account_ownership(account_id: int, seller_id: int) -> bool:
    account = await get_account(account_id)
    if account is None or account["seller_id"] != seller_id:
        raise ServiceError("Akkaunt topilmadi")
    await db.execute(
        "UPDATE accounts SET verification_status = 'verified', is_approved = 1 WHERE id = ?",
        (account["id"],),
    )
    return True


async def get_account(account_id: int) -> dict | None:
    return await db.fetchone(
        "SELECT * FROM accounts WHERE id = ? OR display_id = ?",
        (account_id, account_id),
    )


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


# --------------------------------------------------------------------------
# Test accounts
# --------------------------------------------------------------------------
TEST_SELLER_TG_ID = -1
TEST_SELLER_PHONE = "+998000000000"
TEST_SELLER_PASSWORD = "password123"

TEST_ACCOUNT_TEMPLATES: tuple[tuple[str, str, int], ...] = (
    ("Telegram Premium • 6 oylik obuna faol", "Shaxsiy foydalanish uchun toza akkaunt, barcha emojilar va yulduzlar faol, spam-blok yo'q.", 150_000),
    ("Telegram Shaxsiy Akkaunt • 2019-yil (Eski)", "5 yillik toza tarixli akkaunt, hech qanday cheklovlarsiz, 2FA toza holatda.", 80_000),
    ("Telegram Kanal Admin Akkaunt • 10K obunachi", "IT va texnologiya mavzusidagi kanal admin huquqi bilan birga topshiriladi.", 450_000),
    ("Telegram Biznes Akkaunt • Bot va Stars ulangan", "Biznes xabarlar, avto-javoblar va maxsus username sozlangan profil.", 220_000),
    ("Telegram USA (+1) Raqamli Akkaunt", "Virtual xavfsiz raqamda ochilgan, faol begona sessiyalar yo'q, xavfsiz.", 60_000),
    ("Telegram Guruh Admin Akkaunt • 5 000 a'zo", "Faol savdo-sotiq guruhi boshqaruvchisi, toza reputatsiyaga ega.", 190_000),
    ("Telegram Crypto & Stars Akkaunt • Wallet faol", "Tonkeeper va Telegram Wallet ulangan, tranzaksiyalar tarixi toza.", 300_000),
    ("Telegram Eski O'zbekiston (+998) Akkaunt", "2021-yilda ochilgan, do'stlar va kontaktlar bilan faol akkaunt.", 75_000),
    ("Telegram Dasturchi Akkaunt • API ID & Hash", "Botfather'da tasdiqlangan botlar mavjud, cheklovsiz API kirish imkoni.", 120_000),
    ("Telegram Premium Yillik Akkaunt • VIP Badge", "1 yillik rasmiy Premium obunasi bor, noyob yulduzli profil badge.", 280_000),
)


async def seed_test_accounts(max_accounts: int = TEST_MAX_ACCOUNTS, target_mode: str = "real") -> int:
    """Ensure the system test seller exists with up to ``max_accounts`` test accounts.

    Creates realistic sample listings with IDs 10001, 10002... verified and ready for trade testing.
    """
    system = await users_service.ensure_user(
        TEST_SELLER_TG_ID, "Test Sotuvchi", "test_seller"
    )
    if not system["is_seller"]:
        system = await users_service.register_seller(system["id"], TEST_SELLER_PHONE)
    existing = sum(
        1 for account in await list_seller_accounts(system["id"]) if account["mode"] == target_mode
    )
    remaining = max(0, max_accounts - existing)
    created = 0
    for offset in range(remaining):
        idx = existing + offset
        title, description, price = TEST_ACCOUNT_TEMPLATES[idx % len(TEST_ACCOUNT_TEMPLATES)]
        login = f"+99890{1000000 + idx}"
        display_id = 10001 + idx
        # Ensure display_id doesn't collide with existing
        exists = await db.fetchone("SELECT 1 AS f FROM accounts WHERE display_id = ?", (display_id,))
        if exists:
            display_id = await _next_display_id()
        await create_account(
            seller_id=system["id"],
            title=title,
            description=description,
            login=login,
            password=TEST_SELLER_PASSWORD,
            price=price,
            mode=target_mode,
            photo_id="test_photo_sample",
            extra_info="2FA paroli: 123456 | Qayta tiklash emaili yo'q",
            verification_status="verified",
            display_id=display_id,
        )
        created += 1
    return created


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
        "AND a.verification_status = 'verified' AND u.is_blocked = 0",
        (mode,),
    )
    total = int(total_row["c"]) if total_row else 0
    accounts = await db.fetchall(
        "SELECT a.*, u.full_name AS seller_name, u.seller_code AS seller_code, "
        "u.is_verified AS seller_verified, u.rating AS seller_rating "
        "FROM accounts a JOIN users u ON u.id = a.seller_id "
        "WHERE a.status = 'active' AND a.mode = ? AND a.is_approved = 1 "
        "AND a.verification_status = 'verified' AND u.is_blocked = 0 "
        "ORDER BY COALESCE(a.display_id, a.id) DESC LIMIT ? OFFSET ?",
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
# --------------------------------------------------------------------------
# Orders & Escrow
# --------------------------------------------------------------------------
async def create_escrow_order(buyer_id: int, account_id: int) -> tuple[dict, dict, dict]:
    """Buyer initiates account purchase.

    1. Account is atomically set to 'reserved'.
    2. A 5-minute timer is started (payment_deadline = now + 5 min).
    3. Order is created in 'pending_payment' status.
    4. Credentials remain strictly encrypted and inaccessible until payment is confirmed.
    """
    now = utcnow()
    payment_deadline = (now + timedelta(minutes=5)).isoformat(timespec="seconds")
    check_deadline = (now + timedelta(minutes=5 + CHECK_MINUTES)).isoformat(timespec="seconds")
    auto_complete_at = (
        now + timedelta(minutes=5 + CHECK_MINUTES + AUTO_COMPLETE_MINUTES)
    ).isoformat(timespec="seconds")

    async with db.transaction() as ex:
        buyer = await ex.fetchone("SELECT * FROM users WHERE id = ?", (buyer_id,))
        if buyer is None:
            raise ServiceError("Foydalanuvchi topilmadi")
        if buyer["is_blocked"]:
            raise ServiceError("Hisobingiz bloklangan")

        account = await ex.fetchone(
            "SELECT * FROM accounts WHERE id = ? OR display_id = ?",
            (account_id, account_id),
        )
        if account is None:
            raise ServiceError("Akkaunt topilmadi")
        if account["status"] == "reserved":
            raise ServiceError("Bu akkaunt hozirda boshqa xaridor tomonidan band qilingan (5 daqiqa kutilmoqda)")
        if account["status"] != "active":
            raise ServiceError("Bu akkaunt sotuvda emas")
        if account["seller_id"] == buyer_id:
            raise ServiceError("O'z akkauntingizni sotib ola olmaysiz")

        seller = await ex.fetchone("SELECT * FROM users WHERE id = ?", (account["seller_id"],))
        if seller is None or seller["is_blocked"]:
            raise ServiceError("Sotuvchi faol emas")

        # Concurrency safety: atomic claim
        claimed = await ex.execute(
            "UPDATE accounts SET status = 'reserved' WHERE id = ? AND status = 'active'",
            (account["id"],),
        )
        if claimed == 0:
            raise ServiceError("Bu akkaunt allaqachon boshqa xaridor tomonidan band qilindi")

        order_id = await ex.insert(
            "INSERT INTO orders "
            "(account_id, seller_id, buyer_id, price, status, mode, payment_deadline, "
            "check_deadline, auto_complete_at, created_at) "
            "VALUES (?, ?, ?, ?, 'pending_payment', ?, ?, ?, ?, ?)",
            (
                account["id"],
                account["seller_id"],
                buyer_id,
                account["price"],
                account["mode"],
                payment_deadline,
                check_deadline,
                auto_complete_at,
                utcnow_iso(),
            ),
        )
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma yaratib bo'lmadi")
    return order, account, seller


async def create_order(buyer_id: int, account_id: int) -> tuple[dict, dict, dict]:
    """Legacy instant balance order fallback (used for tests or internal balance)."""
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

        account = await ex.fetchone("SELECT * FROM accounts WHERE id = ? OR display_id = ?", (account_id, account_id))
        if account is None:
            raise ServiceError("Akkaunt topilmadi")
        if account["status"] != "active":
            raise ServiceError("Bu akkaunt allaqachon sotilgan yoki band qilingan")
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
            (utcnow_iso(), account["id"]),
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
                account["id"],
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
                f"Akkaunt #{account.get('display_id', account['id'])} xaridi uchun escrow",
                utcnow_iso(),
            ),
        )
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma yaratib bo'lmadi")
    return order, account, seller


async def confirm_escrow_payment(order_id: int, payment_id: str = "") -> dict:
    """Invoked when official API confirms payment received.

    Transitions order from 'pending_payment' -> 'checking' and marks account 'sold'.
    Credentials now become accessible to buyer.
    """
    now = utcnow()
    check_deadline = (now + timedelta(minutes=CHECK_MINUTES)).isoformat(timespec="seconds")
    auto_complete_at = (
        now + timedelta(minutes=CHECK_MINUTES + AUTO_COMPLETE_MINUTES)
    ).isoformat(timespec="seconds")
    now_iso = utcnow_iso()

    async with db.transaction() as ex:
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma topilmadi")
        if order["status"] == "checking":
            # Idempotent response
            account = await ex.fetchone("SELECT * FROM accounts WHERE id = ?", (order["account_id"],))
            buyer = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["buyer_id"],))
            seller = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["seller_id"],))
            return {"order": order, "account": account, "buyer": buyer, "seller": seller}
        if order["status"] != "pending_payment":
            raise ServiceError(f"Buyurtma kutilmagan holatda: {order['status']}")

        account = await ex.fetchone("SELECT * FROM accounts WHERE id = ?", (order["account_id"],))
        if account is None:
            raise ServiceError("Akkaunt topilmadi")

        pid = payment_id or order.get("payment_id") or ""
        await ex.execute(
            "UPDATE orders SET status = 'checking', payment_id = ?, check_deadline = ?, "
            "auto_complete_at = ? WHERE id = ?",
            (pid, check_deadline, auto_complete_at, order_id),
        )
        await ex.execute(
            "UPDATE accounts SET status = 'sold', sold_at = ? WHERE id = ?",
            (now_iso, account["id"]),
        )
        await ex.execute(
            "UPDATE payments SET status = 'paid', completed_at = ? WHERE order_id = ?",
            (now_iso, order_id),
        )
        await ex.execute(
            "INSERT INTO transactions "
            "(user_id, order_id, amount, type, mode, reference, description, created_at) "
            "VALUES (?, ?, ?, 'purchase', ?, ?, ?, ?)",
            (
                order["buyer_id"],
                order_id,
                -int(order["price"]),
                order["mode"],
                f"order:{order_id}:escrow",
                f"Akkaunt #{account.get('display_id', account['id'])} xaridi uchun to'lov",
                now_iso,
            ),
        )
        updated_order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        buyer = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["buyer_id"],))
        seller = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["seller_id"],))
        return {
            "order": updated_order,
            "account": account,
            "buyer": buyer,
            "seller": seller,
        }


async def cancel_expired_order(order_id: int) -> dict | None:
    """Cancels a pending order when 5 minute window expires and restores account to active."""
    async with db.transaction() as ex:
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None or order["status"] != "pending_payment":
            return None
        now_iso = utcnow_iso()
        await ex.execute(
            "UPDATE orders SET status = 'cancelled', completed_at = ? WHERE id = ?",
            (now_iso, order_id),
        )
        await ex.execute(
            "UPDATE accounts SET status = 'active' WHERE id = ? AND status = 'reserved'",
            (order["account_id"],),
        )
        await ex.execute(
            "UPDATE payments SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'",
            (order_id,),
        )
        buyer = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["buyer_id"],))
        seller = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["seller_id"],))
        account = await ex.fetchone("SELECT * FROM accounts WHERE id = ?", (order["account_id"],))
        return {
            "order": order,
            "buyer": buyer,
            "seller": seller,
            "account": account,
        }


async def expire_pending_orders(bot=None) -> list[dict]:
    """Scans all orders with expired 5-minute payment deadlines.

    Strict rule: Checks official API before cancelling to prevent false cancellations.
    """
    now_iso = utcnow_iso()
    rows = await db.fetchall(
        "SELECT id, payment_id FROM orders WHERE status = 'pending_payment' AND payment_deadline < ?",
        (now_iso,),
    )
    from services import hamyon as hamyon_service
    expired_events = []
    for row in rows:
        order_id = row["id"]
        payment_id = row.get("payment_id") or ""
        is_paid = False
        if payment_id:
            try:
                res = await hamyon_service.check_status(payment_id)
                st = str(res.get("status") or res.get("state") or "").lower()
                if st == "paid":
                    is_paid = True
            except Exception:
                pass
        if is_paid:
            # Payment confirmed just in time!
            info = await confirm_escrow_payment(order_id, payment_id)
            if bot and info.get("buyer") and info.get("account"):
                login, pwd, extra = await get_credentials(order_id, info["order"]["buyer_id"])
                from services import notifications
                await notifications.notify(
                    bot,
                    info["buyer"]["tg_id"],
                    info["buyer"]["lang"],
                    "shop.purchased",
                    title=info["account"]["title"],
                    login=login,
                    password=pwd,
                    price=fmt_sum(info["order"]["price"]),
                    deadline=fmt_dt(info["order"]["check_deadline"]),
                    minutes=CHECK_MINUTES,
                    interval=PENALTY_INTERVAL_MINUTES,
                    penalty=PENALTY_PERCENT,
                )
        else:
            if payment_id:
                try:
                    await hamyon_service.cancel_payment(payment_id)
                except Exception:
                    pass
            cancelled = await cancel_expired_order(order_id)
            if cancelled:
                expired_events.append(cancelled)
    return expired_events


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


async def get_credentials(order_id: int, buyer_id: int) -> tuple[str, str, str]:
    order = await get_order(order_id)
    if order is None:
        raise ServiceError("Buyurtma topilmadi")
    if order["buyer_id"] != buyer_id:
        raise ServiceError("Bu buyurtma sizga tegishli emas")
    if order["status"] == "pending_payment":
        raise ServiceError("To'lov rasmiy API orqali tasdiqlanmaguncha ma'lumotlar ochilmaydi")
    if order["status"] not in ("checking", "completed", "auto_completed", "disputed"):
        raise ServiceError("Bu buyurtma bo'yicha login-parol ko'rsatilmaydi")
    account = await get_account(order["account_id"])
    if account is None:
        raise ServiceError("Akkaunt topilmadi")
    try:
        login = security.decrypt(account["login_enc"])
        password = security.decrypt(account["password_enc"])
        extra = account.get("extra_info") or ""
        return login, password, extra
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
    # Sync with seller_balances table (Section 17)
    balance_row = await ex.fetchone(
        "SELECT id FROM seller_balances WHERE seller_id = ?",
        (order["seller_id"],),
    )
    if balance_row is None:
        await ex.execute(
            "INSERT INTO seller_balances (seller_id, available_balance, frozen_balance, total_earned, updated_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (order["seller_id"], seller_share, seller_share, now),
        )
    else:
        await ex.execute(
            "UPDATE seller_balances SET available_balance = available_balance + ?, "
            "total_earned = total_earned + ?, updated_at = ? WHERE seller_id = ?",
            (seller_share, seller_share, now, order["seller_id"]),
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
            raise ServiceError("Shikoyat ko'rib chiqilmoqda. Tizim avtomatik qarorini kuting")
        if order["status"] != "checking":
            raise ServiceError("Bu buyurtma allaqachon yakunlangan")
        return await _settle_order(ex, order, "completed")


# --------------------------------------------------------------------------
# Automated Dispute Resolution & Cancellation (NO ADMIN INTERVENTION)
# --------------------------------------------------------------------------
async def request_order_cancellation(
    order_id: int, buyer_id: int, reason: str, evidence_text: str = ""
) -> dict:
    """Initiates purchase cancellation (Section 10 & 11).

    Saves buyer's complaint and prompts seller for verification.
    """
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 1000:
        raise ServiceError("Shikoyat sababi kamida 3 belgi bo'lishi kerak")
    now = utcnow_iso()
    async with db.transaction() as ex:
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        if order is None:
            raise ServiceError("Buyurtma topilmadi")
        if order["buyer_id"] != buyer_id:
            raise ServiceError("Bu buyurtma sizga tegishli emas")
        if order["status"] != "checking":
            raise ServiceError("Faqat tekshirish jarayonidagi buyurtmani bekor qilish mumkin")

        await ex.execute(
            "UPDATE orders SET status = 'disputed', cancel_requested_at = ? WHERE id = ?",
            (now, order_id),
        )
        dispute_id = await ex.insert(
            "INSERT INTO disputes (order_id, buyer_id, reason, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (order_id, buyer_id, reason, now),
        )
        if evidence_text:
            await ex.insert(
                "INSERT INTO dispute_evidence (dispute_id, sender_id, evidence_type, content, created_at) "
                "VALUES (?, ?, 'text', ?, ?)",
                (dispute_id, buyer_id, evidence_text[:1000], now),
            )
        await ex.execute(
            "UPDATE users SET active_disputes = active_disputes + 1 WHERE id IN (?, ?)",
            (buyer_id, order["seller_id"]),
        )
    return await get_dispute_details(order_id)


async def _execute_refund_inside_tx(
    ex, order: dict, dispute: dict, decision: str, decision_reason: str, restore_account: bool = True
) -> dict:
    """Executes automated refund to buyer's balance, logs refund & transaction records."""
    now = utcnow_iso()
    total = int(order["price"]) + int(order["penalty"])
    test = order["mode"] == "test"
    balance_field = _balance_field(test)

    # 1. Credit buyer balance
    await ex.execute(
        f"UPDATE users SET {balance_field} = {balance_field} + ? WHERE id = ?",
        (total, order["buyer_id"]),
    )
    # 2. Update order
    await ex.execute(
        "UPDATE orders SET status = 'refunded', completed_at = ? WHERE id = ?",
        (now, order["id"]),
    )
    # 3. Restore account status
    if restore_account:
        await ex.execute(
            "UPDATE accounts SET status = 'active', sold_at = NULL WHERE id = ?",
            (order["account_id"],),
        )
    else:
        await ex.execute(
            "UPDATE accounts SET status = 'paused' WHERE id = ?",
            (order["account_id"],),
        )
    # 4. Insert refund record (Section 17 & 13)
    await ex.insert(
        "INSERT INTO refunds (order_id, payment_id, buyer_id, amount, status, reason, created_at) "
        "VALUES (?, ?, ?, ?, 'completed', ?, ?)",
        (order["id"], order.get("payment_id") or "", order["buyer_id"], total, decision_reason, now),
    )
    # 5. Insert transaction record
    await ex.execute(
        "INSERT INTO transactions "
        "(user_id, order_id, amount, type, mode, reference, description, created_at) "
        "VALUES (?, ?, ?, 'refund', ?, ?, ?, ?)",
        (
            order["buyer_id"],
            order["id"],
            total,
            order["mode"],
            f"order:{order['id']}:refund",
            f"Buyurtma #{order['id']} uchun avtomatik qaytarildi ({decision_reason})"[:500],
            now,
        ),
    )
    # 6. Update dispute
    await ex.execute(
        "UPDATE disputes SET status = 'refunded', auto_resolved = 1, decision = ?, "
        "decision_reason = ?, resolved_at = ? WHERE order_id = ?",
        (decision, decision_reason, now, order["id"]),
    )
    buyer = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["buyer_id"],))
    seller = await ex.fetchone("SELECT tg_id, lang FROM users WHERE id = ?", (order["seller_id"],))
    return {
        "action": "refund",
        "order_id": order["id"],
        "total": total,
        "decision": decision,
        "decision_reason": decision_reason,
        "buyer_tg": buyer["tg_id"] if buyer else None,
        "buyer_lang": buyer["lang"] if buyer else None,
        "seller_tg": seller["tg_id"] if seller else None,
        "seller_lang": seller["lang"] if seller else None,
    }


async def handle_seller_dispute_response(order_id: int, seller_id: int, response_choice: str) -> dict:
    """Automated dispute resolution engine (Section 11 & 12).

    Admin does not intervene. Algorithm decides based on seller answer and objective risk factors.
    """
    choice = response_choice.strip().upper()
    if choice not in ("HA", "YO_Q", "YO'Q", "YES", "NO"):
        raise ServiceError("Noto'g'ri tanlov")
    is_yes = choice in ("HA", "YES")
    choice_str = "HA" if is_yes else "YO_Q"

    async with db.transaction() as ex:
        order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        dispute = await ex.fetchone("SELECT * FROM disputes WHERE order_id = ?", (order_id,))
        if order is None or dispute is None:
            raise ServiceError("Nizo topilmadi")
        if order["seller_id"] != seller_id:
            raise ServiceError("Bu buyurtma sizga tegishli emas")
        if dispute["status"] != "pending":
            raise ServiceError("Bu nizo allaqachon hal qilingan")
        if dispute.get("seller_response"):
            raise ServiceError("Siz allaqachon javob bergansiz")

        now = utcnow_iso()
        await ex.execute(
            "UPDATE disputes SET seller_response = ?, seller_response_at = ? WHERE order_id = ?",
            (choice_str, now, order_id),
        )
        await ex.execute(
            "UPDATE orders SET seller_response = ?, seller_response_at = ? WHERE id = ?",
            (choice_str, now, order_id),
        )

        seller = await ex.fetchone("SELECT * FROM users WHERE id = ?", (seller_id,))
        buyer = await ex.fetchone("SELECT * FROM users WHERE id = ?", (order["buyer_id"],))

        # --- ARBITRATION CASES (Section 12) ---
        if is_yes:
            # Case 1: Seller confirms account is untouched and intact
            decision = "buyer_refunded"
            decision_reason = "Sotuvchi akkaunt joyida va o'zgartirilmaganini tasdiqladi (HA)"
            result = await _execute_refund_inside_tx(
                ex, order, dispute, decision, decision_reason, restore_account=True
            )
            return result
        else:
            # Case 2: Seller claims account is altered / missing (YO'Q)
            # Evaluate independent evidence and reputational track record
            seller_failed = int(seller.get("failed_disputes") or 0)
            seller_sales = int(seller.get("rating_count") or 0)
            buyer_failed = int(buyer.get("failed_disputes") or 0)

            if seller_failed >= 2:
                # Repeatedly problematic seller: favor buyer
                decision = "buyer_refunded"
                decision_reason = "Sotuvchining avvalgi nizolari va shikoyatlari yuqori deb baholandi"
                await ex.execute(
                    "UPDATE users SET failed_disputes = failed_disputes + 1 WHERE id = ?",
                    (seller_id,),
                )
                result = await _execute_refund_inside_tx(
                    ex, order, dispute, decision, decision_reason, restore_account=False
                )
                return result

            if buyer_failed >= 3 and seller_sales >= 3:
                # Abusive buyer profile with no history of seller violations
                decision = "seller_released"
                decision_reason = "Xaridorning asossiz shikoyatlari ko'pligi inobatga olindi"
                await ex.execute(
                    "UPDATE users SET failed_disputes = failed_disputes + 1 WHERE id = ?",
                    (order["buyer_id"],),
                )
                result = await _settle_order(ex, order, "completed")
                await ex.execute(
                    "UPDATE disputes SET status = 'released', auto_resolved = 1, decision = ?, "
                    "decision_reason = ?, resolved_at = ? WHERE order_id = ?",
                    (decision, decision_reason, now, order_id),
                )
                result["action"] = "release"
                result["decision"] = decision
                result["decision_reason"] = decision_reason
                return result

            # Case 3: Inconclusive / conflicting claims with clean records -> Lock funds in escrow
            decision = "locked_hold"
            decision_reason = "Dalillar yetarli emas: mablag' xavfsiz hold holatida saqlanmoqda"
            await ex.execute(
                "UPDATE disputes SET status = 'locked_hold', auto_resolved = 1, decision = ?, "
                "decision_reason = ?, resolved_at = ? WHERE order_id = ?",
                (decision, decision_reason, now, order_id),
            )
            return {
                "action": "hold",
                "order_id": order_id,
                "decision": decision,
                "decision_reason": decision_reason,
                "buyer_tg": buyer["tg_id"],
                "buyer_lang": buyer["lang"],
                "seller_tg": seller["tg_id"],
                "seller_lang": seller["lang"],
            }


async def check_dispute_timeouts(bot=None) -> list[dict]:
    """If seller has not answered HA/YO'Q prompt within 15 minutes,

    resolves dispute automatically in favor of the buyer (unresponsive seller timeout).
    """
    timeout_threshold = (utcnow() - timedelta(minutes=15)).isoformat(timespec="seconds")
    disputes = await db.fetchall(
        "SELECT d.*, o.price, o.penalty, o.seller_id, o.buyer_id, o.mode, o.account_id "
        "FROM disputes d JOIN orders o ON o.id = d.order_id "
        "WHERE d.status = 'pending' AND d.seller_response IS NULL AND d.created_at < ?",
        (timeout_threshold,),
    )
    resolved_events = []
    for d in disputes:
        async with db.transaction() as ex:
            order = await ex.fetchone("SELECT * FROM orders WHERE id = ?", (d["order_id"],))
            dispute = await ex.fetchone("SELECT * FROM disputes WHERE id = ?", (d["id"],))
            if not order or not dispute or dispute["status"] != "pending":
                continue
            decision = "buyer_refunded"
            decision_reason = "Sotuvchi belgilangan 15 daqiqa ichida javob bermadi (Avto-refund)"
            result = await _execute_refund_inside_tx(
                ex, order, dispute, decision, decision_reason, restore_account=True
            )
            resolved_events.append(result)
    return resolved_events


async def create_dispute(order_id: int, buyer_id: int, reason: str) -> dict:
    return await request_order_cancellation(order_id, buyer_id, reason)


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
    val = int(query)
    seller = await db.fetchone(
        "SELECT * FROM users WHERE (seller_code = ? OR id = ?) AND is_seller = 1 AND is_blocked = 0",
        (query, val),
    )
    if seller is not None:
        return {"type": "seller", "seller": seller}
    account = await get_account(val)
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
