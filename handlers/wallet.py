"""Wallet: top-ups, transaction history and requests."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import (
    MAX_TOPUP,
    MIN_TOPUP,
    PAYMENT_CARD,
    PAYMENT_HOLDER,
    PAYMENT_PROVIDER,
)
from handlers.helpers import is_admin_user, respond
from handlers.views import render_wallet
from keyboards.inline import (
    back_keyboard,
    click_payment_keyboard,
    hamyon_payment_keyboard,
)
from keyboards.reply import cancel_reply, main_menu_reply
from services import click as click_service
from services import hamyon as hamyon_service
from services import notifications, payment
from services.errors import ServiceError
from services.i18n import t
from states import TopupFlow
from utils import fmt_dt, fmt_sum, parse_amount

router = Router(name="wallet")


@router.callback_query(F.data == "menu:wallet")
async def wallet(callback: CallbackQuery, user: dict, lang: str) -> None:
    await callback.answer()
    await render_wallet(callback, user, lang)


@router.callback_query(F.data == "wallet:topup")
async def topup_start(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    if user["test_mode"]:
        await callback.answer(t(lang, "wallet.test_mode_no_topup"), show_alert=True)
        return
    await state.set_state(TopupFlow.amount)
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "wallet.topup_title", min=fmt_sum(MIN_TOPUP), max=fmt_sum(MAX_TOPUP)),
            reply_markup=cancel_reply(lang),
        )
    await callback.answer()


@router.message(StateFilter(TopupFlow.amount))
async def topup_amount(
    message: Message, user: dict, lang: str, state: FSMContext
) -> None:
    amount = parse_amount(message.text)
    if amount is None or amount < MIN_TOPUP or amount > MAX_TOPUP:
        await message.answer(
            t(
                lang,
                "wallet.amount_invalid",
                min=fmt_sum(MIN_TOPUP),
                max=fmt_sum(MAX_TOPUP),
            )
        )
        return

    if PAYMENT_PROVIDER == "click":
        if not click_service.is_configured():
            await state.clear()
            await message.answer(t(lang, "wallet.click_unavailable"))
            return
        try:
            topup = await payment.create_click_topup(user["id"], amount)
        except ServiceError as exc:
            await message.answer(str(exc))
            return
        await state.clear()
        await message.answer(
            t(lang, "wallet.click_title", amount=fmt_sum(topup["amount"])),
            reply_markup=click_payment_keyboard(
                lang, click_service.build_payment_url(topup)
            ),
        )
        return

    if PAYMENT_PROVIDER == "hamyon":
        if not hamyon_service.is_configured():
            await state.clear()
            await message.answer(t(lang, "wallet.hamyon_unavailable"))
            return
        topup = None
        try:
            topup = await payment.create_hamyon_topup(user["id"], amount)
            created = await hamyon_service.create_payment(
                int(topup["id"]), int(topup["amount"])
            )
        except ServiceError as exc:
            if topup is not None:
                await payment.cancel_hamyon_topup(int(topup["id"]))
            await message.answer(str(exc))
            return
        await payment.mark_hamyon_created(int(topup["id"]), created["payment_id"])
        await state.clear()
        await message.answer(
            t(
                lang,
                "wallet.hamyon_title",
                amount=fmt_sum(topup["amount"]),
                card=created["card"],
            ),
            reply_markup=hamyon_payment_keyboard(lang, int(topup["id"])),
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(TopupFlow.receipt)
    await message.answer(
        t(
            lang,
            "wallet.payment_info",
            amount=fmt_sum(amount),
            card=PAYMENT_CARD,
            holder=PAYMENT_HOLDER,
        ),
        reply_markup=cancel_reply(lang),
    )


async def _hamyon_topup_or_answer(
    callback: CallbackQuery, topup_id: int, lang: str
) -> dict | None:
    topup = await payment.get_hamyon_topup(payment_id="", order_id=topup_id)
    if topup is None:
        await callback.answer(t(lang, "wallet.hamyon_not_found"), show_alert=True)
    return topup


@router.callback_query(F.data.startswith("hamyon:check:"))
async def hamyon_check(callback: CallbackQuery, user: dict, lang: str) -> None:
    topup_id = int(str(callback.data).split(":", 2)[2])
    topup = await _hamyon_topup_or_answer(callback, topup_id, lang)
    if topup is None:
        return
    payment_id = str(topup.get("receipt_value") or "").strip()
    if not payment_id:
        await callback.answer(t(lang, "wallet.hamyon_pending"), show_alert=True)
        return
    if topup["status"] == "approved":
        await callback.answer(t(lang, "wallet.hamyon_paid"), show_alert=True)
        return
    if topup["status"] != "pending":
        await callback.answer(t(lang, "wallet.hamyon_canceled"), show_alert=True)
        return
    try:
        status = await hamyon_service.check_status(payment_id)
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    state = str(status.get("status") or status.get("state") or "").strip().lower()
    if state == "paid":
        info = await payment.complete_hamyon_topup(topup_id, payment_id)
        if info is not None and not info.get("already"):
            bot = callback.bot
            if bot is not None:
                await notifications.notify(
                    bot,
                    info["user_tg"],
                    info.get("user_lang"),
                    "wallet.topup_approved",
                    amount=fmt_sum(info["amount"]),
                )
        await callback.answer(t(lang, "wallet.hamyon_paid"), show_alert=True)
        return
    if state in ("cancel", "cancelled", "canceled"):
        await payment.cancel_hamyon_topup(topup_id, payment_id)
        await callback.answer(t(lang, "wallet.hamyon_canceled"), show_alert=True)
        return
    await callback.answer(t(lang, "wallet.hamyon_pending"), show_alert=True)


@router.callback_query(F.data.startswith("hamyon:cancel:"))
async def hamyon_cancel(callback: CallbackQuery, user: dict, lang: str) -> None:
    topup_id = int(str(callback.data).split(":", 2)[2])
    topup = await _hamyon_topup_or_answer(callback, topup_id, lang)
    if topup is None:
        return
    if topup["status"] == "approved":
        await callback.answer(t(lang, "wallet.hamyon_paid"), show_alert=True)
        return
    payment_id = str(topup.get("receipt_value") or "").strip()
    if payment_id:
        try:
            await hamyon_service.cancel_payment(payment_id)
        except ServiceError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await payment.cancel_hamyon_topup(topup_id, payment_id)
    await callback.answer(t(lang, "wallet.hamyon_canceled"), show_alert=True)


@router.message(StateFilter(TopupFlow.receipt))
async def topup_receipt(
    message: Message, user: dict, lang: str, state: FSMContext, bot: Bot
) -> None:
    receipt_type: str | None = None
    receipt_value: str | None = None
    if message.photo:
        receipt_type = "photo"
        receipt_value = message.photo[-1].file_id
    elif message.document:
        receipt_type = "document"
        receipt_value = message.document.file_id
    elif message.text:
        receipt_type = "text"
        receipt_value = message.text.strip()
        if len(receipt_value) < 4:
            await message.answer(t(lang, "wallet.receipt_too_short"))
            return
    if receipt_type is None or receipt_value is None:
        await message.answer(t(lang, "wallet.receipt_invalid"))
        return

    data = await state.get_data()
    amount = int(data.get("amount", 0))
    try:
        topup = await payment.create_topup(user["id"], amount, receipt_type, receipt_value)
    except ServiceError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        t(lang, "wallet.topup_created", id=topup["id"], amount=fmt_sum(topup["amount"])),
        reply_markup=main_menu_reply(lang, bool(user["is_seller"]), is_admin_user(user)),
    )
    info = dict(topup)
    info.update(
        user_name=user["full_name"],
        user_tg=user["tg_id"],
        username=user["username"],
    )
    await notifications.topup_created_admins(bot, info)


@router.callback_query(F.data == "wallet:history")
async def history(callback: CallbackQuery, user: dict, lang: str) -> None:
    mode = "test" if user["test_mode"] else "real"
    rows = await payment.list_transactions(user["id"], mode)
    if not rows:
        await callback.answer()

        await respond(callback, t(lang, "wallet.no_transactions"), back_keyboard(lang, "menu:wallet"))
        return
    lines = []
    for row in rows:
        sign = "+" if int(row["amount"]) > 0 else "-"
        label = t(lang, f"tx.type.{row['type']}")
        lines.append(
            t(
                lang,
                "wallet.tx_line",
                label=label,
                sign=sign,
                amount=fmt_sum(abs(int(row["amount"]))),
                date=fmt_dt(row["created_at"]),
            )
        )
    text = t(lang, "wallet.history") + "\n\n" + "\n".join(lines)

    await callback.answer()
    await respond(callback, text, back_keyboard(lang, "menu:wallet"))


@router.callback_query(F.data == "wallet:requests")
async def requests_history(callback: CallbackQuery, user: dict, lang: str) -> None:
    topups = await payment.list_user_topups(user["id"])
    withdrawals = await payment.list_user_withdrawals(user["id"])
    if not topups and not withdrawals:
        await callback.answer()

        await respond(callback, t(lang, "wallet.no_requests"), back_keyboard(lang, "menu:wallet"))
        return

    def status_label(status: str) -> str:
        key = "approved" if status == "paid" else status
        return t(lang, f"wallet.status.{key}")

    blocks: list[str] = []
    if topups:
        rows = [
            t(
                lang,
                "wallet.request_line",
                id=row["id"],
                amount=fmt_sum(row["amount"]),
                status=status_label(row["status"]),
                date=fmt_dt(row["created_at"]),
            )
            for row in topups
        ]
        blocks.append(t(lang, "wallet.topups_section") + "\n" + "\n".join(rows))
    if withdrawals:
        rows = [
            t(
                lang,
                "wallet.request_line",
                id=row["id"],
                amount=fmt_sum(row["amount"]),
                status=status_label(row["status"]),
                date=fmt_dt(row["created_at"]),
            )
            for row in withdrawals
        ]
        blocks.append(t(lang, "wallet.withdrawals_section") + "\n" + "\n".join(rows))
    text = t(lang, "wallet.requests_title") + "\n\n" + "\n\n".join(blocks)

    await callback.answer()
    await respond(callback, text, back_keyboard(lang, "menu:wallet"))
