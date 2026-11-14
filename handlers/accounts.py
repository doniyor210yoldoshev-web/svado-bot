"""Marketplace browsing and order lifecycle handlers."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import (
    CHECK_MINUTES,
    PAYMENT_PROVIDER,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
)
from handlers.helpers import is_admin_user, respond
from handlers.views import (
    render_order_detail,
    render_orders,
    render_shop,
    render_shop_detail,
)
from keyboards.inline import (
    admin_dispute_keyboard,
    back_keyboard,
    buy_confirm_keyboard,
    order_detail_keyboard,
    order_payment_keyboard,
    shop_payment_choice_keyboard,
)
from keyboards.reply import cancel_reply, main_menu_reply
from services import hamyon as hamyon_service
from services import marketplace, notifications, payment
from services import users as users_service
from services.errors import InsufficientFunds, ServiceError
from services.i18n import t
from states import DisputeFlow
from utils import esc, fmt_dt, fmt_sum

logger = logging.getLogger(__name__)

router = Router(name="accounts")


def _tail_id(data: str | None) -> int | None:
    try:
        return int((data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _use_external_escrow(user: dict) -> bool:
    return (
        not user["test_mode"]
        and PAYMENT_PROVIDER == "hamyon"
        and hamyon_service.is_configured()
    )


@router.callback_query(F.data.startswith("menu:browse:"))
async def browse(callback: CallbackQuery, user: dict, lang: str) -> None:
    page = _tail_id(callback.data) or 1
    await callback.answer()
    await render_shop(callback, user, lang, page)


@router.callback_query(F.data.startswith("shop:view:"))
async def shop_view(callback: CallbackQuery, user: dict, lang: str) -> None:
    account_id = _tail_id(callback.data)
    if account_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    await callback.answer()
    await render_shop_detail(callback, user, lang, account_id)


@router.callback_query(F.data.startswith("shop:buy:"))
async def shop_buy(callback: CallbackQuery, user: dict, lang: str) -> None:
    account_id = _tail_id(callback.data)
    if account_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    account = await marketplace.get_account(account_id)
    if account is None:
        await callback.answer(t(lang, "common.not_found"), show_alert=True)
        return
    if account["status"] != "active":
        await callback.answer(t(lang, "shop.sold_out"), show_alert=True)
        return
    if account["seller_id"] == user["id"]:
        await callback.answer(t(lang, "shop.own_account"), show_alert=True)
        return
    if _use_external_escrow(user):
        balance = int(user["balance"])
        if balance >= int(account["price"]):
            text = t(
                lang,
                "shop.choose_payment",
                title=esc(account["title"]),
                price=fmt_sum(account["price"]),
                balance=fmt_sum(balance),
            )
            await callback.answer()
            await respond(
                callback,
                text,
                shop_payment_choice_keyboard(lang, account_id, wallet_available=True),
            )
            return
        text = t(
            lang,
            "shop.external_confirm",
            title=esc(account["title"]),
            price=fmt_sum(account["price"]),
        )
    else:
        balance = int(user["test_balance"] if user["test_mode"] else user["balance"])
        if balance < int(account["price"]):
            await callback.answer(
                t(
                    lang,
                    "shop.buy_insufficient",
                    required=fmt_sum(account["price"]),
                    available=fmt_sum(balance),
                ),
                show_alert=True,
            )
            return
        text = t(
            lang,
            "shop.confirm_title",
            title=esc(account["title"]),
            price=fmt_sum(account["price"]),
            balance=fmt_sum(balance),
        )
    await callback.answer()
    await respond(callback, text, buy_confirm_keyboard(lang, account_id))


@router.callback_query(F.data.startswith("shop:confirm:"))
async def shop_confirm(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    account_id = _tail_id(callback.data)
    if account_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    if _use_external_escrow(user):
        try:
            order, account, _seller = await marketplace.create_escrow_order(
                user["id"], account_id
            )
        except ServiceError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        try:
            pay_amount = await payment.unique_hamyon_amount(
                int(order["price"]), exclude_order_id=order["id"]
            )
            pay = await hamyon_service.create_payment(order["id"], pay_amount)
            await marketplace.attach_escrow_payment(
                order["id"],
                user["id"],
                pay["payment_id"],
                pay_amount,
                pay.get("card", ""),
            )
        except ServiceError as exc:
            await marketplace.cancel_expired_order(order["id"])
            await callback.answer(str(exc), show_alert=True)
            return
        await respond(
            callback,
            t(
                lang,
                "shop.payment_title",
                order=order["id"],
                title=esc(account["title"]),
                amount=fmt_sum(pay_amount),
                card=esc(pay["card"]),
                minutes=5,
                deadline=fmt_dt(order["payment_deadline"]),
            ),
            order_payment_keyboard(lang, order["id"]),
        )
        await notifications.notify_admin_event(
            bot,
            "new_order",
            id=order["id"],
            account_id=account.get("display_id", account["id"]),
            price=fmt_sum(order["price"]),
        )
        return

    await _purchase_with_wallet(callback, user, lang, bot, account_id)


async def _purchase_with_wallet(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot, account_id: int
) -> None:
    try:
        order, account, seller = await marketplace.create_order(user["id"], account_id)
        login, password = await marketplace.get_credentials(order["id"], user["id"])
    except InsufficientFunds as exc:
        await callback.answer(
            t(
                lang,
                "wallet.purchase_failed",
                required=fmt_sum(exc.required),
                available=fmt_sum(exc.available),
            ),
            show_alert=True,
        )
        return
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text = t(
        lang,
        "shop.purchased",
        title=esc(account["title"]),
        login=esc(login),
        password=esc(password),
        price=fmt_sum(order["price"]),
        deadline=fmt_dt(order["check_deadline"]),
        minutes=CHECK_MINUTES,
        interval=PENALTY_INTERVAL_MINUTES,
        penalty=PENALTY_PERCENT,
    )
    await callback.answer()
    await respond(callback, text, order_detail_keyboard(lang, order))
    await notifications.sale_pending(bot, order, account, seller)
    await notifications.notify_admin_event(
        bot,
        "new_order",
        id=order["id"],
        account_id=account.get("display_id", account["id"]),
        price=fmt_sum(order["price"]),
    )


@router.callback_query(F.data.startswith("shop:wallet:"))
async def shop_wallet_pay(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    """Explicitly pay for a marketplace item from the internal wallet."""
    account_id = _tail_id(callback.data)
    if account_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    if user["test_mode"]:
        await callback.answer(t(lang, "shop.wallet_test_mode"), show_alert=True)
        return
    await _purchase_with_wallet(callback, user, lang, bot, account_id)


@router.callback_query(F.data.startswith("order:paycheck:"))
async def order_pay_check(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    order_id = _tail_id(callback.data)
    if order_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    order = await marketplace.get_order(order_id)
    if order is None:
        await callback.answer(t(lang, "order.not_found"), show_alert=True)
        return
    if order["buyer_id"] != user["id"]:
        await callback.answer(t(lang, "order.not_owner"), show_alert=True)
        return
    if order["status"] == "cancelled":
        await callback.answer(t(lang, "shop.payment_cancelled"), show_alert=True)
        return
    payment_id = str(order.get("payment_id") or "")
    if order["status"] != "pending_payment" or not payment_id:
        await callback.answer(t(lang, "shop.payment_no_payment"), show_alert=True)
        return
    try:
        result = await hamyon_service.check_status(payment_id)
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    status = str(result.get("status") or result.get("state") or "").lower()
    if status in ("paid", "success", "completed"):
        try:
            info = await marketplace.confirm_escrow_payment(order_id, payment_id)
            login, password = await marketplace.get_credentials(order_id, user["id"])
        except ServiceError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await respond(
            callback,
            t(
                lang,
                "shop.purchased",
                title=esc(info["account"]["title"]),
                login=esc(login),
                password=esc(password),
                price=fmt_sum(info["order"]["price"]),
                deadline=fmt_dt(info["order"]["check_deadline"]),
                minutes=CHECK_MINUTES,
                interval=PENALTY_INTERVAL_MINUTES,
                penalty=PENALTY_PERCENT,
            ),
            order_detail_keyboard(lang, info["order"]),
        )
        await notifications.sale_pending(
            bot, info["order"], info["account"], info["seller"]
        )
        await notifications.notify_admin_event(
            bot,
            "payment_confirmed",
            order_id=order_id,
            amount=fmt_sum(info["order"]["price"]),
        )
        return
    if status in ("cancel", "cancelled", "canceled", "failed", "error"):
        await marketplace.cancel_expired_order(order_id)
        await respond(
            callback,
            t(lang, "shop.payment_cancelled"),
            back_keyboard(lang, "menu:browse:1"),
        )
        return
    await callback.answer(t(lang, "shop.payment_pending"), show_alert=True)


@router.callback_query(F.data.startswith("order:paycancel:"))
async def order_pay_cancel(
    callback: CallbackQuery, user: dict, lang: str
) -> None:
    order_id = _tail_id(callback.data)
    if order_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    order = await marketplace.get_order(order_id)
    if order is None:
        await callback.answer(t(lang, "order.not_found"), show_alert=True)
        return
    if order["buyer_id"] != user["id"]:
        await callback.answer(t(lang, "order.not_owner"), show_alert=True)
        return
    payment_id = str(order.get("payment_id") or "")
    if payment_id:
        try:
            await hamyon_service.cancel_payment(payment_id)
        except ServiceError as exc:
            logger.warning("To'lovni API orqali bekor qilishda xatolik: %s", exc)
    try:
        await marketplace.cancel_pending_order(order_id, user["id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await respond(
        callback,
        t(lang, "shop.payment_cancelled"),
        back_keyboard(lang, "menu:browse:1"),
    )


@router.callback_query(F.data.startswith("disp:seller:"))
async def seller_dispute_answer(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    answer, raw_order_id = parts[2], parts[3]
    try:
        order_id = int(raw_order_id)
    except ValueError:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    choice = "HA" if answer == "ha" else "YO_Q"
    try:
        info = await marketplace.handle_seller_dispute_response(
            order_id, user["id"], choice
        )
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t(lang, "dispute.seller_answered"))
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:  # noqa: BLE001 - markup may already be cleared
            logger.warning("Inline klaviaturani tozalab bo'lmadi: %s", exc)
    await notifications.dispute_resolved(bot, info)
    await notifications.notify_admin_event(
        bot,
        "fraud_alert",
        order_id=order_id,
        reason=f"Sotuvchi javobi: {choice}",
        decision=info.get("decision_reason") or info.get("action"),
    )


@router.callback_query(F.data.startswith("order:page:"))
async def order_page(callback: CallbackQuery, user: dict, lang: str) -> None:
    page = _tail_id(callback.data) or 1
    await callback.answer()
    await render_orders(callback, user, lang, page)


@router.callback_query(F.data.startswith("order:view:"))
async def order_view(callback: CallbackQuery, user: dict, lang: str) -> None:
    order_id = _tail_id(callback.data)
    if order_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    await callback.answer()
    await render_order_detail(callback, user, lang, order_id)


@router.callback_query(F.data.startswith("order:creds:"))
async def order_creds(callback: CallbackQuery, user: dict, lang: str) -> None:
    order_id = _tail_id(callback.data)
    if order_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        login, password = await marketplace.get_credentials(order_id, user["id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "order.creds_title", id=order_id, login=esc(login), password=esc(password))
        )
    await callback.answer()


@router.callback_query(F.data.startswith("order:confirm:"))
async def order_confirm(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    order_id = _tail_id(callback.data)
    if order_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        info = await marketplace.confirm_order(order_id, user["id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await respond(
        callback,
        t(
            lang,
            "order.confirmed",
            id=order_id,
            penalty=fmt_sum(info["penalty"]),
            total=fmt_sum(info["total"]),
        ),
        back_keyboard(lang, "order:page:1"),
    )
    await notifications.sale_completed(bot, info)
    await notifications.notify_admin_event(
        bot,
        "trade_completed",
        order_id=info["order_id"],
        share=fmt_sum(info["seller_share"]),
        commission=fmt_sum(info["commission"]),
    )


@router.callback_query(F.data.startswith("order:dispute:"))
async def order_dispute(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    order_id = _tail_id(callback.data)
    if order_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    order = await marketplace.get_order(order_id)
    if order is None:
        await callback.answer(t(lang, "order.not_found"), show_alert=True)
        return
    if order["buyer_id"] != user["id"]:
        await callback.answer(t(lang, "order.not_owner"), show_alert=True)
        return
    if order["status"] != "checking":
        await callback.answer(t(lang, "order.cannot_dispute"), show_alert=True)
        return
    await state.set_state(DisputeFlow.reason)
    await state.update_data(order_id=order_id)
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "order.dispute_prompt", id=order_id),
            reply_markup=cancel_reply(lang),
        )
    await callback.answer()


@router.message(StateFilter(DisputeFlow.reason))
async def dispute_reason(
    message: Message, state: FSMContext, user: dict, lang: str
) -> None:
    reason = (message.text or "").strip()
    if not 3 <= len(reason) <= 1000:
        await message.answer(t(lang, "order.dispute_reason_invalid"))
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    if not isinstance(order_id, int):
        await state.clear()
        await message.answer(t(lang, "common.invalid_data"))
        return
    await state.update_data(reason=reason)
    await state.set_state(DisputeFlow.evidence)
    await message.answer(
        t(lang, "order.dispute_evidence_prompt"),
        reply_markup=cancel_reply(lang),
    )


@router.message(StateFilter(DisputeFlow.evidence))
async def dispute_evidence(
    message: Message, state: FSMContext, user: dict, lang: str, bot: Bot
) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    reason = data.get("reason", "")
    if not isinstance(order_id, int) or not reason:
        await state.clear()
        await message.answer(t(lang, "common.invalid_data"))
        return

    text = (message.text or "").strip()
    is_skip = text in ("-",) or any(
        text == t(lang_, "common.none") for lang_ in ("uz", "ru", "en")
    )
    if message.photo:
        evidence_type, evidence_content = "photo", message.photo[-1].file_id
    elif message.document:
        evidence_type, evidence_content = "document", message.document.file_id
    elif is_skip:
        evidence_type, evidence_content = "", ""
    elif text:
        if len(text) > 1000:
            await message.answer(t(lang, "order.dispute_evidence_too_long"))
            return
        evidence_type, evidence_content = "text", text
    else:
        await message.answer(t(lang, "order.dispute_evidence_invalid"))
        return

    try:
        await marketplace.create_dispute(
            order_id,
            user["id"],
            reason,
            evidence_type=evidence_type or "text",
            evidence_content=evidence_content,
        )
    except ServiceError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        t(lang, "order.dispute_created", id=order_id),
        reply_markup=main_menu_reply(lang, bool(user["is_seller"]), is_admin_user(user)),
    )
    details = await marketplace.get_dispute_details(order_id)
    if details is None:
        return
    await notifications.dispute_opened(bot, details)
    for admin_tg, admin_lang in await users_service.admin_langs():
        await notifications.notify(
            bot,
            admin_tg,
            admin_lang,
            "admin.dispute_card",
            reply_markup=admin_dispute_keyboard(admin_lang, order_id),
            order=order_id,
            account=details.get("account_display_id") or details["account_id"],
            buyer=esc(details["buyer_name"]) or "—",
            buyer_tg=details["buyer_tg"],
            seller=esc(details["seller_name"]) or "—",
            seller_tg=details["seller_tg"],
            price=fmt_sum(details["price"]),
            penalty=fmt_sum(details["penalty"]),
            reason=esc(details["reason"]),
            date=fmt_dt(details["created_at"]),
        )
