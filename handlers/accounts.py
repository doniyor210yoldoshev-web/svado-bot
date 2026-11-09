"""Marketplace browsing and order lifecycle handlers."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import CHECK_MINUTES, PENALTY_INTERVAL_MINUTES, PENALTY_PERCENT
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
)
from keyboards.reply import cancel_reply, main_menu_reply
from services import marketplace, notifications
from services import users as users_service
from services.errors import ServiceError
from services.i18n import t
from states import DisputeFlow
from utils import esc, fmt_dt, fmt_sum

router = Router(name="accounts")


def _tail_id(data: str | None) -> int | None:
    try:
        return int((data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return None


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
    try:
        order, account, seller = await marketplace.create_order(user["id"], account_id)
        login, password = await marketplace.get_credentials(order["id"], user["id"])
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
    await respond(callback, text, order_detail_keyboard(lang, order))
    await notifications.sale_pending(bot, order, account, seller)


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
    message: Message, state: FSMContext, user: dict, lang: str, bot: Bot
) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    if not isinstance(order_id, int):
        await state.clear()
        await message.answer(t(lang, "common.invalid_data"))
        return
    try:
        await marketplace.create_dispute(order_id, user["id"], message.text or "")
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
            account=details["account_id"],
            buyer=esc(details["buyer_name"]) or "—",
            buyer_tg=details["buyer_tg"],
            seller=esc(details["seller_name"]) or "—",
            seller_tg=details["seller_tg"],
            price=fmt_sum(details["price"]),
            penalty=fmt_sum(details["penalty"]),
            reason=esc(details["reason"]),
            date=fmt_dt(details["created_at"]),
        )
