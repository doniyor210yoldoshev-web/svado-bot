"""Click (click.uz) SHOP API integration.

Click sends two form-encoded POST callbacks to the deployed service:

* ``prepare`` (action=0)  -> reserve the order and return ``merchant_prepare_id``
* ``complete`` (action=1) -> confirm the payment and credit the wallet

The signature is an MD5 hash of concatenated fields (no separators). Prepare and
complete use slightly different formulas: complete also includes the
``merchant_prepare_id`` returned by prepare.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from aiogram import Bot

from config import (
    CLICK_MERCHANT_ID,
    CLICK_PAY_URL,
    CLICK_SECRET_KEY,
    CLICK_SERVICE_ID,
    PUBLIC_BASE_URL,
)
from services import notifications, payment
from utils import fmt_sum

logger = logging.getLogger(__name__)

ERRORS: dict[int, str] = {
    0: "Success",
    -1: "SIGN CHECK FAILED",
    -2: "Incorrect parameter amount",
    -3: "Action not found",
    -4: "Already paid",
    -5: "User does not exist",
    -6: "Transaction does not exist",
    -7: "Failed to update user",
    -8: "Error in request from click",
    -9: "Transaction cancelled",
}

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def is_configured() -> bool:
    return bool(CLICK_SERVICE_ID and CLICK_MERCHANT_ID and CLICK_SECRET_KEY)


def prepare_url() -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/click/prepare"
    return "https://<domen>/click/prepare"


def complete_url() -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/click/complete"
    return "https://<domen>/click/complete"


def build_payment_url(topup: dict) -> str:
    params = {
        "service_id": CLICK_SERVICE_ID,
        "merchant_id": CLICK_MERCHANT_ID,
        "amount": str(int(topup["amount"])),
        "transaction_param": str(topup["id"]),
    }
    return f"{CLICK_PAY_URL}?{urlencode(params)}"


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _expected_signature(data: dict[str, str]) -> str:
    action = data.get("action", "")
    parts = [
        data.get("click_trans_id", ""),
        data.get("service_id", CLICK_SERVICE_ID),
        CLICK_SECRET_KEY,
        data.get("merchant_trans_id", ""),
    ]
    if action == "1":
        parts.append(data.get("merchant_prepare_id", ""))
    parts.extend([data.get("amount", ""), action, data.get("sign_time", "")])
    return _md5("".join(parts))


def verify_signature(data: dict[str, str]) -> bool:
    received = (data.get("sign_string") or "").strip().lower()
    if not received:
        return False
    return hmac.compare_digest(_expected_signature(data), received)


def _as_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _amount_matches(raw: object, amount: int) -> bool:
    try:
        value = Decimal(str(raw).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return False
    return value == Decimal(amount)


def _error(
    code: int,
    data: dict[str, str],
    prepare_id: int | None = None,
    confirm_id: int | None = None,
) -> dict:
    response = {
        "click_trans_id": data.get("click_trans_id", ""),
        "merchant_trans_id": data.get("merchant_trans_id", ""),
        "error": code,
        "error_note": ERRORS.get(code, "Error"),
    }
    if prepare_id is not None:
        response["merchant_prepare_id"] = prepare_id
    if confirm_id is not None:
        response["merchant_confirm_id"] = confirm_id
    return response


def _success(
    data: dict[str, str], prepare_id: int | None = None, confirm_id: int | None = None
) -> dict:
    return _error(0, data, prepare_id=prepare_id, confirm_id=confirm_id)


async def handle_request(data: dict[str, str]) -> dict:
    action = (data.get("action") or "").strip()
    if action == "0":
        return await _prepare(data)
    if action == "1":
        return await _complete(data)
    return _error(-3, data)


async def _prepare(data: dict[str, str]) -> dict:
    if not is_configured():
        logger.error(
            "Click sozlanmagan: CLICK_SERVICE_ID, CLICK_MERCHANT_ID va CLICK_SECRET_KEY kiriting"
        )
        return _error(-8, data)
    if (data.get("service_id") or "").strip() != CLICK_SERVICE_ID:
        return _error(-1, data)
    if not verify_signature(data):
        return _error(-1, data)

    topup_id = _as_int(data.get("merchant_trans_id"))
    if topup_id is None:
        return _error(-5, data)
    topup = await payment.get_click_topup(topup_id)
    if topup is None:
        return _error(-5, data)
    if not _amount_matches(data.get("amount"), int(topup["amount"])):
        return _error(-2, data)
    if topup["status"] == "approved":
        return _error(-4, data)
    if topup["status"] != "pending":
        return _error(-9, data)

    click_trans_id = str(data.get("click_trans_id", ""))
    await payment.mark_click_prepared(topup_id, click_trans_id)
    logger.info("Click prepare: topup=%s click_trans_id=%s", topup_id, click_trans_id)
    return _success(data, prepare_id=topup_id)


async def _complete(data: dict[str, str]) -> dict:
    if not is_configured():
        logger.error(
            "Click sozlanmagan: CLICK_SERVICE_ID, CLICK_MERCHANT_ID va CLICK_SECRET_KEY kiriting"
        )
        return _error(-8, data)
    if (data.get("service_id") or "").strip() != CLICK_SERVICE_ID:
        return _error(-1, data)
    if not verify_signature(data):
        return _error(-1, data)

    topup_id = _as_int(data.get("merchant_prepare_id")) or _as_int(
        data.get("merchant_trans_id")
    )
    if topup_id is None:
        return _error(-6, data)
    topup = await payment.get_click_topup(topup_id)
    if topup is None:
        return _error(-6, data)
    if not _amount_matches(data.get("amount"), int(topup["amount"])):
        return _error(-2, data)

    click_error = _as_int(data.get("error")) or 0
    if click_error < 0:
        await payment.cancel_click_topup(topup_id)
        logger.info(
            "Click to'lov bekor qilindi: topup=%s click_error=%s",
            topup_id,
            click_error,
        )
        return _error(-9, data)

    info = await payment.complete_click_topup(
        topup_id,
        str(data.get("click_trans_id", "")),
        str(data.get("click_paydoc_id", "")),
    )
    if info is None:
        return _error(-6, data)

    if not info.get("already"):
        await _notify_paid(info)
    logger.info(
        "Click complete: topup=%s click_trans_id=%s", topup_id, data.get("click_trans_id")
    )
    return _success(data, confirm_id=topup_id)


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
