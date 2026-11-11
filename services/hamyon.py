"""HamyonAPI (hamyon-api.uz) integration for HUMO/UZCARD transfers.

Flow:

1. The bot creates a top-up and calls ``POST /payment/create`` with
   ``shop_id``, ``shop_key``, ``amount`` and ``order_id`` (our top-up id).
   The response contains ``payment_id`` and the card number shown to the user.
2. The user transfers exactly that amount to the card (HUMO/UZCARD).
3. HamyonAPI watches the card and POSTs callbacks to our service:

   * ``prepare_url`` — the transfer is being detected (pending)
   * ``complete_url`` — ``status=paid`` on success, ``status=cancel``
     (e.g. ``reason=timeout``) after 5 minutes

   Callback signature: ``md5(shop_id + payment_id + amount + shop_key)``.

4. Callbacks may be retried (3/10/30/60s), so crediting is idempotent and
   keyed by ``payment_id``.

Only one open payment per amount is allowed per shop, so amounts are nudged
by 1-2 so'm when a collision happens (see ``payment.create_hamyon_topup``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation

import aiohttp
from aiogram import Bot

from config import (
    HAMYON_API_URL,
    HAMYON_SHOP_ID,
    HAMYON_SHOP_KEY,
    PUBLIC_BASE_URL,
)
from services import notifications, payment
from services.errors import ServiceError
from utils import fmt_sum

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def is_configured() -> bool:
    return bool(HAMYON_SHOP_ID and HAMYON_SHOP_KEY)


def prepare_url() -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/hamyon/prepare"
    return "https://<domen>/hamyon/prepare"


def complete_url() -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/hamyon/complete"
    return "https://<domen>/hamyon/complete"


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------
async def _request(
    method: str, path: str, payload: dict | None = None, params: dict | None = None
) -> dict:
    url = f"{HAMYON_API_URL}{path}"
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.request(method, url, data=payload, params=params) as response,
        ):
            status = response.status
            text = await response.text()
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.error("HamyonAPI so'rovida ulanish xatosi (%s %s): %s", method, path, exc)
        raise ServiceError(
            "To'lov serveriga ulanib bo'lmadi. Keyinroq urinib ko'ring."
        ) from exc

    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        logger.error(
            "HamyonAPI noto'g'ri javob (%s %s, HTTP %s): %s", method, path, status, text
        )
        raise ServiceError(
            "To'lov serveridan javob olinmadi. Keyinroq urinib ko'ring."
        ) from None
    if not isinstance(body, dict):
        logger.error(
            "HamyonAPI noto'g'ri javob (%s %s, HTTP %s): %s", method, path, status, text
        )
        raise ServiceError(
            "To'lov serveridan javob olinmadi. Keyinroq urinib ko'ring."
        ) from None
    return body


def _extract_card(payload: dict) -> str:
    for key in ("card", "card_number", "karta", "card_mask", "cards"):
        value = str(payload.get(key, "")).strip()
        if value and value.lower() not in ("none", "null"):
            return value
    return ""


async def create_payment(order_id: int, amount: int) -> dict:
    """Create a HamyonAPI payment. Returns ``{"payment_id": ..., "card": ...}``."""
    body = await _request(
        "POST",
        "/payment/create",
        {
            "shop_id": HAMYON_SHOP_ID,
            "shop_key": HAMYON_SHOP_KEY,
            "amount": int(amount),
            "order_id": str(order_id),
        },
    )
    if body.get("error") or not body.get("payment_id"):
        message = str(body.get("error") or body.get("message") or "error")
        logger.error("HamyonAPI to'lov yaratish xatosi: %s", message)
        raise ServiceError(
            f"To'lovni yaratib bo'lmadi: {message}"
        )
    card = _extract_card(body)
    if not card:
        logger.error("HamyonAPI javobida karta raqami yo'q: %s", body)
        raise ServiceError(
            "To'lovni yaratib bo'lmadi. Keyinroq urinib ko'ring."
        )
    return {"payment_id": str(body["payment_id"]), "card": card}


async def check_status(payment_id: str) -> dict:
    """Read the payment status (optional polling endpoint)."""
    return await _request(
        "GET",
        "/payment/status",
        params={"payment_id": payment_id, "shop_id": HAMYON_SHOP_ID},
    )


async def cancel_payment(payment_id: str) -> dict:
    """Close the payment before its 5 minute deadline."""
    return await _request(
        "POST",
        "/payment/cancel",
        {
            "shop_id": HAMYON_SHOP_ID,
            "shop_key": HAMYON_SHOP_KEY,
            "payment_id": payment_id,
        },
    )


# --------------------------------------------------------------------------
# Callback verification and processing
# --------------------------------------------------------------------------
def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def verify_signature(data: dict[str, str]) -> bool:
    """md5(shop_id + payment_id + amount + shop_key) computed on raw values."""
    received = (data.get("sign") or "").strip().lower()
    if not received:
        return False
    expected = _md5(
        "".join(
            (
                data.get("shop_id", HAMYON_SHOP_ID),
                str(data.get("payment_id", "")),
                str(data.get("amount", "")),
                HAMYON_SHOP_KEY,
            )
        )
    )
    return hmac.compare_digest(expected, received)


def _amount_matches(raw: object, amount: int) -> bool:
    try:
        value = Decimal(str(raw).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return False
    return value == Decimal(amount)


def _as_int(raw: object) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


async def handle_callback(data: dict[str, str]) -> dict:
    """Process a HamyonAPI prepare/complete callback.

    Always answers ``{"ok": ...}``; HamyonAPI expects HTTP 200 with any body.
    Callbacks can be retried, so all branches are idempotent.
    """
    if not is_configured():
        logger.error(
            "HamyonAPI sozlanmagan: HAMYON_SHOP_ID va HAMYON_SHOP_KEY kiriting"
        )
        return {"ok": False}
    shop_id = (data.get("shop_id") or "").strip()
    if shop_id and shop_id != HAMYON_SHOP_ID:
        logger.warning("Hamyon callback noto'g'ri shop_id: %s", shop_id)
        return {"ok": False}
    if not verify_signature(data):
        logger.warning("Hamyon callback imzosi noto'g'ri: %s", data)
        return {"ok": False}

    payment_id = str(data.get("payment_id", "")).strip()
    status = str(data.get("status", "")).strip().lower()
    order_id = _as_int(data.get("order_id"))
    reason = str(data.get("reason", "")).strip()

    if not payment_id:
        return {"ok": False}

    if status == "paid":
        await _handle_paid(payment_id, order_id, data)
        return {"ok": True}
    if status == "cancel":
        await _handle_cancel(payment_id, order_id, reason)
        return {"ok": True}
    if status in ("prepare", "pending", "created"):
        logger.info("Hamyon prepare: payment_id=%s order_id=%s", payment_id, order_id)
        return {"ok": True}
    logger.warning("Hamyon callback noma'lum status: %s (payment_id=%s)", status, payment_id)
    return {"ok": True}


async def _handle_paid(payment_id: str, order_id: int | None, data: dict[str, str]) -> None:
    # 1. Check if this is an escrow account order
    from database.db import db
    from services import marketplace
    order = None
    if order_id is not None:
        order = await db.fetchone(
            "SELECT * FROM orders WHERE id = ? AND status = 'pending_payment'",
            (order_id,),
        )
    if order is None and payment_id:
        order = await db.fetchone(
            "SELECT * FROM orders WHERE payment_id = ? AND status = 'pending_payment'",
            (payment_id,),
        )

    if order is not None:
        if not _amount_matches(data.get("amount"), int(order["price"])):
            logger.error(
                "Hamyon paid (Order): summa mos emas (order=%s, expected=%s, got=%s)",
                order["id"],
                order["price"],
                data.get("amount"),
            )
            return
        info = await marketplace.confirm_escrow_payment(order["id"], payment_id)
        if _bot is not None and info.get("buyer") and info.get("account"):
            from config import CHECK_MINUTES, PENALTY_INTERVAL_MINUTES, PENALTY_PERCENT
            login, pwd, extra = await marketplace.get_credentials(order["id"], order["buyer_id"])
            await notifications.notify(
                _bot,
                info["buyer"]["tg_id"],
                info["buyer"]["lang"],
                "shop.purchased",
                title=info["account"]["title"],
                login=login,
                password=pwd,
                price=fmt_sum(order["price"]),
                deadline=fmt_dt(info["order"]["check_deadline"]),
                minutes=CHECK_MINUTES,
                interval=PENALTY_INTERVAL_MINUTES,
                penalty=PENALTY_PERCENT,
            )
            await notifications.notify(
                _bot,
                info["seller"]["tg_id"],
                info["seller"]["lang"],
                "notify.seller_sold",
                title=info["account"]["title"],
                price=fmt_sum(order["price"]),
                order=order["id"],
                share=93,
            )
            await notifications.notify_admin_event(
                _bot,
                "payment_confirmed",
                order_id=order["id"],
                amount=fmt_sum(order["price"]),
            )
        logger.info("Hamyon paid (Order): order=%s payment_id=%s", order["id"], payment_id)
        return

    # 2. Check if this is a wallet topup
    topup = await payment.get_hamyon_topup(payment_id=payment_id, order_id=order_id)
    if topup is None:
        logger.warning(
            "Hamyon paid: to'lov topilmadi (payment_id=%s order_id=%s)",
            payment_id,
            order_id,
        )
        return
    if not _amount_matches(data.get("amount"), int(topup["amount"])):
        logger.error(
            "Hamyon paid: summa mos emas (topup=%s, expected=%s, got=%s)",
            topup["id"],
            topup["amount"],
            data.get("amount"),
        )
        return
    info = await payment.complete_hamyon_topup(int(topup["id"]), payment_id)
    if info is None:
        return
    if not info.get("already"):
        await _notify_paid(info)
    logger.info("Hamyon paid: topup=%s payment_id=%s", topup["id"], payment_id)


async def _handle_cancel(payment_id: str, order_id: int | None, reason: str) -> None:
    from database.db import db
    from services import marketplace
    order = None
    if order_id is not None:
        order = await db.fetchone(
            "SELECT * FROM orders WHERE id = ? AND status = 'pending_payment'",
            (order_id,),
        )
    if order is None and payment_id:
        order = await db.fetchone(
            "SELECT * FROM orders WHERE payment_id = ? AND status = 'pending_payment'",
            (payment_id,),
        )
    if order is not None:
        cancelled = await marketplace.cancel_expired_order(order["id"])
        if _bot is not None and cancelled and cancelled.get("buyer"):
            await notifications.notify(
                _bot,
                cancelled["buyer"]["tg_id"],
                cancelled["buyer"]["lang"],
                "shop.order_cancelled_timeout",
                order=order["id"],
            )
            await notifications.notify_admin_event(
                _bot,
                "order_cancelled",
                order_id=order["id"],
            )
        logger.info("Hamyon cancel (Order): order=%s payment_id=%s", order["id"], payment_id)
        return

    topup = await payment.get_hamyon_topup(payment_id=payment_id, order_id=order_id)
    if topup is None:
        logger.info(
            "Hamyon cancel: to'lov topilmadi (payment_id=%s order_id=%s)",
            payment_id,
            order_id,
        )
        return
    await payment.cancel_hamyon_topup(int(topup["id"]), payment_id)
    if _bot is not None:
        await notifications.notify(
            _bot,
            topup["user_tg"],
            topup.get("user_lang"),
            "wallet.hamyon_canceled",
        )
    logger.info(
        "Hamyon cancel: topup=%s payment_id=%s reason=%s", topup["id"], payment_id, reason
    )


async def _notify_paid(info: dict) -> None:
    if _bot is None or not info.get("user_tg"):
        return
    await notifications.notify(
        _bot,
        info["user_tg"],
        info.get("user_lang"),
        "wallet.topup_approved",
        amount=fmt_sum(info["amount"]),
    )
