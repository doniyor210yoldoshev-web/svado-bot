"""Admin panel handlers."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import TEST_INITIAL_BALANCE
from handlers.helpers import respond
from keyboards.inline import (
    admin_menu_keyboard,
    admin_user_keyboard,
    back_keyboard,
    broadcast_keyboard,
    test_keyboard,
)
from keyboards.reply import cancel_reply, main_menu_reply
from services import marketplace, notifications, payment
from services import users as users_service
from services.errors import ServiceError
from services.i18n import t
from states import AdminFlow
from utils import esc, fmt_dt, fmt_sum, parse_amount

router = Router(name="admin")


class AdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("user")
        if not user or not user.get("is_admin"):
            lang = data.get("lang") or "uz"
            text = t(lang, "admin.only_admin")
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None
        return await handler(event, data)


router.callback_query.outer_middleware(AdminMiddleware())
router.message.outer_middleware(AdminMiddleware())


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _tail_id(data: str | None) -> int | None:
    try:
        return int((data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _page(data: str | None) -> int:
    try:
        return max(1, int((data or "").rsplit(":", 1)[1]))
    except (ValueError, IndexError):
        return 1


def _pager(prefix: str, page: int, total: int, lang: str) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if page > 1:
        row.append(_btn(t(lang, "common.prev"), f"{prefix}:{page - 1}"))
    row.append(_btn(t(lang, "common.page", page=page, total=total), "noop"))
    if page < total:
        row.append(_btn(t(lang, "common.next"), f"{prefix}:{page + 1}"))
    return row


@router.callback_query(F.data == "admin:home")
async def home(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await respond(callback, t(lang, "admin.title"), admin_menu_keyboard(lang))


@router.callback_query(F.data == "admin:stats")
async def stats(callback: CallbackQuery, lang: str) -> None:
    data = await marketplace.admin_stats()
    text = t(
        lang,
        "admin.stats",
        users=data["users"],
        sellers=data["sellers"],
        active=data["active"],
        sold=data["sold"],
        checking=data["checking"],
        completed=data["completed"],
        disputed=data["disputed"],
        gmv=fmt_sum(data["gmv"]),
        commission=fmt_sum(data["commission"]),
        topups=data["topups"],
        withdrawals=data["withdrawals"],
    )
    await callback.answer()
    await respond(callback, text, back_keyboard(lang, "admin:home"))


async def _render_payments(callback: CallbackQuery, lang: str, page: int) -> None:
    rows = await payment.list_pending_topups(limit=200)
    if not rows:
        await respond(callback, t(lang, "admin.no_pending"), back_keyboard(lang, "admin:home"))
        return
    page = min(max(1, page), len(rows))
    row = rows[page - 1]
    text = t(
        lang,
        "admin.topup_card",
        name=esc(row["user_name"]) or "—",
        tg=row["user_tg"],
        username=esc(row.get("username")) or "—",
        amount=fmt_sum(row["amount"]),
        receipt_type=row["receipt_type"],
        date=fmt_dt(row["created_at"]),
        id=row["id"],
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        _btn(t(lang, "admin.approve"), f"admin:topup:ok:{row['id']}"),
        _btn(t(lang, "admin.reject"), f"admin:topup:no:{row['id']}"),
    )
    kb.row(*_pager("admin:payments", page, len(rows), lang))
    kb.row(_btn(t(lang, "admin.back"), "admin:home"))
    await respond(callback, text, kb.as_markup())


@router.callback_query(F.data.startswith("admin:payments:"))
async def payments(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await _render_payments(callback, lang, _page(callback.data))


@router.callback_query(F.data.startswith("admin:topup:ok:"))
async def topup_approve(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    topup_id = _tail_id(callback.data)
    if topup_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        info = await payment.approve_topup(topup_id, user["tg_id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await notifications.topup_reviewed(bot, info, True)
    await marketplace.log_audit(user["tg_id"], "topup.approve", f"#{topup_id}")
    await callback.answer(t(lang, "admin.topup_ok"))
    await _render_payments(callback, lang, 1)


@router.callback_query(F.data.startswith("admin:topup:no:"))
async def topup_reject(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    topup_id = _tail_id(callback.data)
    if topup_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        info = await payment.reject_topup(topup_id, user["tg_id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await notifications.topup_reviewed(bot, info, False)
    await marketplace.log_audit(user["tg_id"], "topup.reject", f"#{topup_id}")
    await callback.answer(t(lang, "admin.topup_no"))
    await _render_payments(callback, lang, 1)


async def _render_withdrawals(callback: CallbackQuery, lang: str, page: int) -> None:
    rows = await payment.list_pending_withdrawals(limit=200)
    if not rows:
        await respond(
            callback, t(lang, "admin.no_pending"), back_keyboard(lang, "admin:home")
        )
        return
    page = min(max(1, page), len(rows))
    row = rows[page - 1]
    text = t(
        lang,
        "admin.withdrawal_card",
        name=esc(row["user_name"]) or "—",
        tg=row["user_tg"],
        code=esc(row.get("seller_code")) or "—",
        amount=fmt_sum(row["amount"]),
        card=row["card_number"],
        date=fmt_dt(row["created_at"]),
        id=row["id"],
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        _btn(t(lang, "admin.paid"), f"admin:wd:ok:{row['id']}"),
        _btn(t(lang, "admin.reject"), f"admin:wd:no:{row['id']}"),
    )
    kb.row(*_pager("admin:withdrawals", page, len(rows), lang))
    kb.row(_btn(t(lang, "admin.back"), "admin:home"))
    await respond(callback, text, kb.as_markup())


@router.callback_query(F.data.startswith("admin:withdrawals:"))
async def withdrawals(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await _render_withdrawals(callback, lang, _page(callback.data))


@router.callback_query(F.data.startswith("admin:wd:ok:"))
async def withdrawal_approve(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    withdrawal_id = _tail_id(callback.data)
    if withdrawal_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        info = await payment.approve_withdrawal(withdrawal_id, user["tg_id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await notifications.withdrawal_reviewed(bot, info, True)
    await marketplace.log_audit(user["tg_id"], "withdrawal.paid", f"#{withdrawal_id}")
    await callback.answer(t(lang, "admin.withdrawal_ok"))
    await _render_withdrawals(callback, lang, 1)


@router.callback_query(F.data.startswith("admin:wd:no:"))
async def withdrawal_reject(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    withdrawal_id = _tail_id(callback.data)
    if withdrawal_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        info = await payment.reject_withdrawal(withdrawal_id, user["tg_id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await notifications.withdrawal_reviewed(bot, info, False)
    await marketplace.log_audit(user["tg_id"], "withdrawal.reject", f"#{withdrawal_id}")
    await callback.answer(t(lang, "admin.withdrawal_no"))
    await _render_withdrawals(callback, lang, 1)


async def _render_disputes(callback: CallbackQuery, lang: str, page: int) -> None:
    rows = await marketplace.list_pending_disputes(limit=200)
    if not rows:
        await respond(
            callback, t(lang, "admin.no_disputes"), back_keyboard(lang, "admin:home")
        )
        return
    page = min(max(1, page), len(rows))
    row = rows[page - 1]
    text = t(
        lang,
        "admin.dispute_card",
        order=row["order_id"],
        account=row["account_id"],
        buyer=esc(row["buyer_name"]) or "—",
        buyer_tg=row["buyer_tg"],
        seller=esc(row["seller_name"]) or "—",
        seller_tg=row["seller_tg"],
        price=fmt_sum(row["price"]),
        penalty=fmt_sum(row["penalty"]),
        reason=esc(row["reason"]),
        date=fmt_dt(row["created_at"]),
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        _btn(t(lang, "admin.refund"), f"admin:disp:refund:{row['order_id']}"),
        _btn(t(lang, "admin.release"), f"admin:disp:release:{row['order_id']}"),
    )
    kb.row(_btn(t(lang, "admin.check_creds"), f"admin:disp:creds:{row['order_id']}"))
    kb.row(*_pager("admin:disputes", page, len(rows), lang))
    kb.row(_btn(t(lang, "admin.back"), "admin:home"))
    await respond(callback, text, kb.as_markup())


@router.callback_query(F.data.startswith("admin:disputes:"))
async def disputes(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await _render_disputes(callback, lang, _page(callback.data))


@router.callback_query(F.data.startswith("admin:disp:creds:"))
async def dispute_creds(callback: CallbackQuery, lang: str) -> None:
    order_id = _tail_id(callback.data)
    if order_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        login, password = await marketplace.get_credentials_admin(order_id)
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if callback.message is not None:
        await callback.message.answer(
            t(
                lang,
                "admin.dispute_creds",
                order=order_id,
                login=esc(login),
                password=esc(password),
            )
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:disp:refund:"))
@router.callback_query(F.data.startswith("admin:disp:release:"))
async def dispute_resolve(
    callback: CallbackQuery, user: dict, lang: str, bot: Bot
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    action = parts[2]
    try:
        order_id = int(parts[3])
    except ValueError:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        info = await marketplace.resolve_dispute(order_id, action, user["tg_id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await notifications.dispute_resolved(bot, info)
    await marketplace.log_audit(user["tg_id"], f"dispute.{action}", f"#{order_id}")
    if action == "refund":
        message = t(
            lang,
            "admin.dispute_refunded",
            order=order_id,
            amount=fmt_sum(info["total"]),
        )
    else:
        message = t(lang, "admin.dispute_released", order=order_id)
    await callback.answer(message)
    await _render_disputes(callback, lang, 1)


@router.callback_query(F.data == "admin:sales")
async def sales(callback: CallbackQuery, lang: str) -> None:
    orders = await marketplace.list_recent_sales(30)
    if not orders:
        await callback.answer()
        await respond(callback, t(lang, "admin.no_sales"), back_keyboard(lang, "admin:home"))
        return
    lines = [
        f"#{order['id']} | {t(lang, 'order.status.' + order['status'])} | "
        f"{fmt_sum(int(order['price']) + int(order['penalty']))} | "
        f"{esc(order['seller_name'])} -> {esc(order['buyer_name'])}"
        for order in orders
    ]
    text = t(lang, "admin.sales_btn") + "\n\n" + "\n".join(lines)
    await callback.answer()
    await respond(callback, text, back_keyboard(lang, "admin:home"))


@router.callback_query(F.data == "admin:accounts")
async def accounts(callback: CallbackQuery, lang: str) -> None:
    rows = await marketplace.admin_list_accounts(30)
    if not rows:
        await callback.answer()
        await respond(
            callback, t(lang, "admin.accounts_empty"), back_keyboard(lang, "admin:home")
        )
        return
    lines = [
        t(
            lang,
            "admin.accounts_line",
            id=row["id"],
            status=t(lang, "account.status." + row["status"]),
            price=fmt_sum(row["price"]),
            seller=esc(row["seller_name"]) or "—",
        )
        for row in rows
    ]
    text = t(lang, "admin.accounts_title") + "\n\n" + "\n".join(lines)
    await callback.answer()
    await respond(callback, text, back_keyboard(lang, "admin:home"))


@router.callback_query(F.data == "admin:audit")
async def audit(callback: CallbackQuery, lang: str) -> None:
    rows = await marketplace.list_audit_logs(20)
    if not rows:
        await callback.answer()
        await respond(callback, t(lang, "admin.audit_empty"), back_keyboard(lang, "admin:home"))
        return
    lines = [
        t(
            lang,
            "admin.audit_line",
            action=esc(row["action"]),
            actor=row["actor_tg"] or "—",
            details=esc(row["details"]) or "—",
            date=fmt_dt(row["created_at"]),
        )
        for row in rows
    ]
    text = t(lang, "admin.audit_title") + "\n\n" + "\n".join(lines)
    await callback.answer()
    await respond(callback, text, back_keyboard(lang, "admin:home"))


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def _user_card(lang: str, target: dict) -> str:
    role = (
        t(lang, "profile.role_admin")
        if target["is_admin"]
        else t(lang, "profile.role_seller")
        if target["is_seller"]
        else t(lang, "profile.role_buyer")
    )
    status = (
        t(lang, "admin.status_blocked")
        if target["is_blocked"]
        else t(lang, "admin.status_active")
    )
    test_state = t(lang, "admin.on") if target["test_mode"] else t(lang, "admin.off")
    return t(
        lang,
        "admin.user_card",
        id=target["id"],
        tg=target["tg_id"],
        name=esc(target["full_name"]) or "—",
        username=esc(target["username"]) or "—",
        phone=esc(target["phone"]) or "—",
        role=role,
        code=esc(target["seller_code"]) or "—",
        balance=fmt_sum(target["balance"]),
        test_balance=fmt_sum(target["test_balance"]),
        status=status,
        test_mode=test_state,
        date=fmt_dt(target["created_at"]),
    )


async def _render_user(
    event: Message | CallbackQuery, lang: str, target: dict
) -> None:
    await respond(event, _user_card(lang, target), admin_user_keyboard(lang, target))


@router.callback_query(F.data == "admin:users")
async def users_start(
    callback: CallbackQuery, lang: str, state: FSMContext
) -> None:
    await state.set_state(AdminFlow.user_query)
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "admin.user_query_prompt"), reply_markup=cancel_reply(lang)
        )
    await callback.answer()


@router.message(StateFilter(AdminFlow.user_query))
async def users_search(message: Message, lang: str, state: FSMContext) -> None:
    target = await users_service.search_user(message.text or "")
    if target is None:
        await message.answer(t(lang, "admin.user_not_found"))
        return
    await state.clear()
    await _render_user(message, lang, target)


@router.callback_query(F.data.startswith("admin:user:"))
async def user_action(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    action = parts[2]
    try:
        db_id = int(parts[3])
    except ValueError:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    target = await users_service.get_user(db_id)
    if target is None:
        await callback.answer(t(lang, "admin.user_not_found"), show_alert=True)
        return

    if action == "balance":
        await state.set_state(AdminFlow.balance_amount)
        await state.update_data(target_user_id=db_id)
        if callback.message is not None:
            await callback.message.answer(
                t(
                    lang,
                    "admin.balance_prompt",
                    name=esc(target["full_name"]) or "—",
                    tg=target["tg_id"],
                    balance=fmt_sum(target["balance"]),
                ),
                reply_markup=cancel_reply(lang),
            )
        await callback.answer()
        return

    if action == "block":
        await users_service.set_blocked(db_id, True)
        await callback.answer(t(lang, "admin.blocked_ok", tg=target["tg_id"]))
    elif action == "unblock":
        await users_service.set_blocked(db_id, False)
        await callback.answer(t(lang, "admin.unblocked_ok", tg=target["tg_id"]))
    elif action == "verify":
        await users_service.set_verified(db_id, True)
        await callback.answer(t(lang, "admin.verified_ok", tg=target["tg_id"]))
    elif action == "unverify":
        await users_service.set_verified(db_id, False)
        await callback.answer(t(lang, "admin.unverified_ok", tg=target["tg_id"]))
    else:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    await marketplace.log_audit(user["tg_id"], f"user.{action}", f"user#{db_id}")
    fresh = await users_service.get_user(db_id)
    if fresh is not None:
        await _render_user(callback, lang, fresh)


@router.message(StateFilter(AdminFlow.balance_amount))
async def balance_amount(
    message: Message, lang: str, state: FSMContext
) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    negative = raw.startswith("-")
    amount = parse_amount(raw)
    if amount is None or amount == 0:
        await message.answer(t(lang, "admin.balance_amount_invalid"))
        return
    if negative:
        amount = -amount
    await state.update_data(amount=amount)
    await state.set_state(AdminFlow.balance_note)
    await message.answer(
        t(lang, "admin.balance_note_prompt", amount=fmt_sum(amount)),
        reply_markup=cancel_reply(lang),
    )


@router.message(StateFilter(AdminFlow.balance_note))
async def balance_note(
    message: Message, user: dict, lang: str, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    db_id = data.get("target_user_id")
    amount = data.get("amount")
    if not isinstance(db_id, int) or not isinstance(amount, int):
        await state.clear()
        await message.answer(t(lang, "common.invalid_data"))
        return
    note = (message.text or "").strip()
    if note == "-":
        note = ""
    try:
        updated, new_balance = await payment.admin_adjust_balance(
            db_id, amount, note, user["tg_id"]
        )
    except ServiceError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        t(
            lang,
            "admin.balance_updated",
            amount=fmt_sum(amount),
            balance=fmt_sum(new_balance),
        ),
        reply_markup=main_menu_reply(lang, bool(user["is_seller"]), True),
    )
    await notifications.balance_changed(bot, updated, amount, new_balance)
    await marketplace.log_audit(user["tg_id"], "user.balance", f"user#{db_id}: {amount}")
    await _render_user(message, lang, updated)


# --------------------------------------------------------------------------
# Broadcast
# --------------------------------------------------------------------------
@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(
    callback: CallbackQuery, lang: str, state: FSMContext
) -> None:
    await state.set_state(AdminFlow.broadcast_text)
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "admin.broadcast_prompt"), reply_markup=cancel_reply(lang)
        )
    await callback.answer()


@router.message(StateFilter(AdminFlow.broadcast_text))
async def broadcast_preview(message: Message, lang: str, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(t(lang, "common.invalid_data"))
        return
    await state.update_data(broadcast_text=text)
    await message.answer(
        t(lang, "admin.broadcast_preview", text=esc(text)),
        reply_markup=broadcast_keyboard(lang),
    )


@router.callback_query(F.data == "admin:broadcast:send")
async def broadcast_send(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    recipients = await users_service.broadcast_recipients()
    sent = 0
    failed = 0
    for recipient in recipients:
        ok = await notifications.send(bot, recipient["tg_id"], esc(text))
        if ok:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)
    await state.clear()
    await marketplace.log_audit(
        user["tg_id"], "broadcast", f"sent={sent} failed={failed}"
    )
    await callback.answer()
    await respond(
        callback,
        t(lang, "admin.broadcast_done", sent=sent, failed=failed),
        back_keyboard(lang, "admin:home"),
    )


# --------------------------------------------------------------------------
# Test mode
# --------------------------------------------------------------------------
async def _render_test(
    event: Message | CallbackQuery, target: dict, lang: str
) -> None:
    text = t(
        lang,
        "admin.test_title",
        name=esc(target["full_name"]) or "—",
        tg=target["tg_id"],
        state=t(lang, "admin.on") if target["test_mode"] else t(lang, "admin.off"),
        balance=fmt_sum(target["test_balance"]),
    )
    await respond(event, text, test_keyboard(lang, bool(target["test_mode"])))


@router.callback_query(F.data == "admin:test")
async def test_panel(callback: CallbackQuery, user: dict, lang: str) -> None:
    await callback.answer()
    await _render_test(callback, user, lang)


@router.callback_query(F.data == "admin:test:toggle")
async def test_toggle(callback: CallbackQuery, user: dict, lang: str) -> None:
    updated = await users_service.toggle_test_mode(user["id"], TEST_INITIAL_BALANCE)
    key = "admin.test_on_msg" if updated["test_mode"] else "admin.test_off_msg"
    await callback.answer(
        t(lang, key, balance=fmt_sum(updated["test_balance"])), show_alert=True
    )
    await _render_test(callback, updated, lang)


@router.callback_query(F.data == "admin:test:seed")
async def test_seed(callback: CallbackQuery, user: dict, lang: str) -> None:
    system = await users_service.ensure_user(-1, "Test Sotuvchi", "test_seller")
    if not system["is_seller"]:
        system = await users_service.register_seller(system["id"], "+998000000000")
    created = 0
    for index in range(5):
        login = f"test_{secrets.token_hex(4)}"
        await marketplace.create_account(
            system["id"],
            f"Test akkaunt {index + 1}",
            "Sinov uchun avtomatik yaratilgan akkaunt.",
            login,
            "password123",
            10_000,
            "test",
        )
        created += 1
    await callback.answer(
        t(lang, "admin.test_seeded", count=created), show_alert=True
    )
    fresh = await users_service.get_user(user["id"])
    if fresh is not None:
        await _render_test(callback, fresh, lang)
