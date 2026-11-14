"""Seller panel: registration, listings, pricing and withdrawals."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MIN_PRICE, MIN_WITHDRAW
from handlers.helpers import is_admin_user, respond
from handlers.views import (
    render_seller_accounts,
    render_seller_panel,
    render_seller_register,
)
from keyboards.inline import (
    add_confirm_keyboard,
    back_keyboard,
    delete_confirm_keyboard,
    ownership_keyboard,
    seller_account_keyboard,
)
from keyboards.reply import cancel_reply, main_menu_reply
from services import marketplace, notifications, payment
from services import users as users_service
from services.errors import ServiceError
from services.i18n import t
from states import PriceEdit, SellerFlow, WithdrawFlow
from utils import esc, fmt_dt, fmt_sum, parse_amount

router = Router(name="seller")
logger = logging.getLogger(__name__)


def _tail_id(data: str | None) -> int | None:
    try:
        return int((data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return None


async def _menu(message: Message, user: dict, lang: str) -> None:
    await message.answer(
        t(lang, "menu.title"),
        reply_markup=main_menu_reply(lang, bool(user["is_seller"]), is_admin_user(user)),
    )


@router.callback_query(F.data == "seller:panel")
async def panel(callback: CallbackQuery, user: dict, lang: str) -> None:
    await callback.answer()
    await render_seller_panel(callback, user, lang)


@router.callback_query(F.data == "seller:register")
async def register(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    if user["is_seller"]:
        await callback.answer(t(lang, "seller.already_seller"), show_alert=True)
        return
    await state.set_state(SellerFlow.phone)
    await render_seller_register(callback, user, lang)


@router.message(StateFilter(SellerFlow.phone), F.contact)
async def phone_contact(
    message: Message, user: dict, lang: str, state: FSMContext, bot: Bot
) -> None:
    if (
        message.contact.user_id is not None
        and message.contact.user_id != message.from_user.id
    ):
        await message.answer(t(lang, "seller.phone_not_owned"))
        return
    phone = (message.contact.phone_number or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 9:
        await message.answer(t(lang, "seller.phone_invalid"))
        return
    try:
        updated = await users_service.register_seller(user["id"], "+" + digits)
    except ServiceError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        t(lang, "seller.registered", code=updated["seller_code"]),
        reply_markup=main_menu_reply(lang, True, is_admin_user(updated)),
    )
    await notifications.notify_admin_event(
        bot,
        "new_seller",
        name=esc(updated["full_name"]) or "—",
        tg_id=updated["tg_id"],
        code=updated["seller_code"] or "—",
    )


@router.message(StateFilter(SellerFlow.phone))
async def phone_text(message: Message, lang: str) -> None:
    await message.answer(t(lang, "seller.phone_wrong"))


@router.callback_query(F.data.startswith("seller:page:"))
async def accounts_page(callback: CallbackQuery, user: dict, lang: str) -> None:
    page = _tail_id(callback.data) or 1
    await callback.answer()
    await render_seller_accounts(callback, user, lang, page)


@router.callback_query(F.data == "seller:add")
async def add_start(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    if not user["is_seller"]:
        await callback.answer(t(lang, "seller.only_sellers"), show_alert=True)
        return
    await state.set_state(SellerFlow.title)
    await state.update_data(
        title="",
        description="",
        photo_id=None,
        extra_info="",
        login="",
        password="",
        price=0,
    )
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "seller.add_title"), reply_markup=cancel_reply(lang)
        )
    await callback.answer()


@router.message(StateFilter(SellerFlow.title))
async def add_title(message: Message, lang: str, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not 2 <= len(title) <= 100:
        await message.answer(t(lang, "seller.invalid_title"))
        return
    await state.update_data(title=title)
    await state.set_state(SellerFlow.description)
    await message.answer(
        t(lang, "seller.add_desc_prompt"),
        reply_markup=cancel_reply(lang),
    )


@router.message(StateFilter(SellerFlow.description))
async def add_description(message: Message, lang: str, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in ("-",) or any(text == t(lang_, "common.none") for lang_ in ("uz", "ru", "en")):
        text = ""
    if len(text) > 1000:
        await message.answer(t(lang, "seller.invalid_desc"))
        return
    await state.update_data(description=text)
    await state.set_state(SellerFlow.photo)
    await message.answer(
        t(lang, "seller.add_photo_prompt"),
        reply_markup=cancel_reply(lang),
    )


@router.message(StateFilter(SellerFlow.photo))
async def add_photo(message: Message, lang: str, state: FSMContext) -> None:
    text = (message.text or "").strip()
    is_skip = text in ("-",) or any(
        text == t(lang_, "common.none") for lang_ in ("uz", "ru", "en")
    )
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif is_skip:
        photo_id = None
    else:
        await message.answer(t(lang, "seller.photo_invalid"))
        return
    await state.update_data(photo_id=photo_id)
    await state.set_state(SellerFlow.extra_info)
    await message.answer(
        t(lang, "seller.add_extra_prompt"),
        reply_markup=cancel_reply(lang),
    )


@router.message(StateFilter(SellerFlow.extra_info))
async def add_extra_info(message: Message, lang: str, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in ("-",) or any(text == t(lang_, "common.none") for lang_ in ("uz", "ru", "en")):
        text = ""
    if len(text) > 500:
        await message.answer(t(lang, "seller.extra_invalid"))
        return
    await state.update_data(extra_info=text)
    await state.set_state(SellerFlow.login)
    await message.answer(t(lang, "seller.add_login_prompt"), reply_markup=cancel_reply(lang))


@router.message(StateFilter(SellerFlow.login))
async def add_login(message: Message, lang: str, state: FSMContext) -> None:
    login = (message.text or "").strip()
    if not 2 <= len(login) <= 200:
        await message.answer(t(lang, "seller.invalid_login"))
        return
    await state.update_data(login=login)
    await state.set_state(SellerFlow.password)
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001 - deleting is best effort
        logger.debug("Xabarni o'chirishda xatolik: %s", exc)
    await message.answer(t(lang, "seller.add_password_prompt"), reply_markup=cancel_reply(lang))


@router.message(StateFilter(SellerFlow.password))
async def add_password(message: Message, lang: str, state: FSMContext) -> None:
    password = (message.text or "").strip()
    if not 4 <= len(password) <= 200:
        await message.answer(t(lang, "seller.invalid_password"))
        return
    await state.update_data(password=password)
    await state.set_state(SellerFlow.price)
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Xabarni o'chirishda xatolik: %s", exc)
    await message.answer(
        t(lang, "seller.add_price_prompt", min_price=fmt_sum(MIN_PRICE)),
        reply_markup=cancel_reply(lang),
    )


@router.message(StateFilter(SellerFlow.price))
async def add_price(message: Message, lang: str, state: FSMContext) -> None:
    price = parse_amount(message.text)
    if price is None or price < MIN_PRICE:
        await message.answer(t(lang, "seller.price_invalid", min_price=fmt_sum(MIN_PRICE)))
        return
    await state.update_data(price=price)
    await state.set_state(SellerFlow.verify_ownership)
    await message.answer(
        t(lang, "seller.add_ownership_title"),
        reply_markup=ownership_keyboard(lang),
    )


@router.callback_query(F.data == "seller:ownership:yes")
async def ownership_yes(
    callback: CallbackQuery, lang: str, state: FSMContext
) -> None:
    current = await state.get_state()
    if current != SellerFlow.verify_ownership.state:
        await callback.answer(t(lang, "seller.ownership_required"), show_alert=True)
        return
    data = await state.get_data()
    await state.set_state(SellerFlow.confirm)
    await state.update_data(ownership_confirmed=True)
    text = t(
        lang,
        "seller.add_confirm_title",
        title=esc(data.get("title", "")),
        description=esc(data.get("description", "")) or t(lang, "seller.no_description"),
        photo="✅" if data.get("photo_id") else "—",
        extra=esc(data.get("extra_info", "")) or "—",
        login=esc(data.get("login", "")),
        price=fmt_sum(int(data.get("price", 0))),
    )
    await callback.answer()
    await respond(callback, text, add_confirm_keyboard(lang))


@router.callback_query(F.data == "seller:add:yes")
async def add_confirm(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    if not data.get("ownership_confirmed"):
        await callback.answer(t(lang, "seller.ownership_required"), show_alert=True)
        return
    try:
        account_id = await marketplace.create_account(
            user["id"],
            data.get("title", ""),
            data.get("description", ""),
            data.get("login", ""),
            data.get("password", ""),
            int(data.get("price", 0)),
            "real",
            photo_id=data.get("photo_id"),
            extra_info=data.get("extra_info", ""),
            verification_status="verified",
        )
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await callback.answer()
    account = await marketplace.get_account(account_id)
    display_id = int(account.get("display_id") or account_id) if account else account_id
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "seller.added", id=display_id, price=fmt_sum(data.get("price", 0))),
            reply_markup=main_menu_reply(lang, True, is_admin_user(user)),
        )
    await notifications.notify_admin_event(
        bot,
        "new_listing",
        id=display_id,
        title=esc(data.get("title", "")),
        price=fmt_sum(int(data.get("price", 0))),
    )


@router.callback_query(F.data == "seller:add:cancel")
async def add_cancel(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    await state.clear()
    await callback.answer()
    await render_seller_panel(callback, user, lang)


@router.callback_query(F.data.startswith("seller:acc:"))
async def account_detail(callback: CallbackQuery, user: dict, lang: str) -> None:
    account_id = _tail_id(callback.data)
    account = await marketplace.get_account(account_id) if account_id else None
    if account is None or account["seller_id"] != user["id"]:
        await callback.answer(t(lang, "seller.not_owner"), show_alert=True)
        return
    try:
        login = await marketplace.get_account_login(account["id"])
    except ServiceError:
        login = "—"
    text = t(
        lang,
        "seller.account_detail",
        id=account.get("display_id") or account["id"],
        title=esc(account["title"]),
        description=esc(account["description"]) or t(lang, "seller.no_description"),
        login=esc(login),
        price=fmt_sum(account["price"]),
        status=t(lang, "account.status." + account["status"]),
        date=fmt_dt(account["created_at"]),
    )
    await callback.answer()

    await respond(callback, text, seller_account_keyboard(lang, account))


@router.callback_query(F.data.startswith("seller:pause:"))
@router.callback_query(F.data.startswith("seller:resume:"))
async def toggle_pause(callback: CallbackQuery, user: dict, lang: str) -> None:
    action, _, account_id = (callback.data or "").split(":")
    try:
        await marketplace.set_account_paused(
            int(account_id), user["id"], paused=action == "pause"
        )
    except (ServiceError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t(lang, "seller.paused_ok" if action == "pause" else "seller.resumed_ok"))
    account = await marketplace.get_account(int(account_id))
    if account is None:
        await render_seller_accounts(callback, user, lang, 1)
        return

    login = "—"
    try:
        login = await marketplace.get_account_login(account["id"])
    except ServiceError:
        pass
    text = t(
        lang,
        "seller.account_detail",
        id=account.get("display_id") or account["id"],
        title=esc(account["title"]),
        description=esc(account["description"]) or t(lang, "seller.no_description"),
        login=esc(login),
        price=fmt_sum(account["price"]),
        status=t(lang, "account.status." + account["status"]),
        date=fmt_dt(account["created_at"]),
    )
    await respond(callback, text, seller_account_keyboard(lang, account))


@router.callback_query(F.data.startswith("seller:del:"))
async def delete_ask(callback: CallbackQuery, user: dict, lang: str) -> None:
    account_id = _tail_id(callback.data)
    account = await marketplace.get_account(account_id) if account_id else None
    if account is None or account["seller_id"] != user["id"]:
        await callback.answer(t(lang, "seller.not_owner"), show_alert=True)
        return
    await callback.answer()

    text = t(
        lang,
        "seller.delete_confirm",
        id=account.get("display_id") or account["id"],
    )
    await respond(callback, text, delete_confirm_keyboard(lang, account["id"]))


@router.callback_query(F.data.startswith("seller:delok:"))
async def delete_confirm(callback: CallbackQuery, user: dict, lang: str) -> None:
    account_id = _tail_id(callback.data)
    if account_id is None:
        await callback.answer(t(lang, "common.invalid_data"), show_alert=True)
        return
    try:
        await marketplace.delete_account(account_id, user["id"])
    except ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t(lang, "seller.deleted"))
    await render_seller_accounts(callback, user, lang, 1)


@router.callback_query(F.data.startswith("seller:price:"))
async def price_ask(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    account_id = _tail_id(callback.data)
    account = await marketplace.get_account(account_id) if account_id else None
    if account is None or account["seller_id"] != user["id"]:
        await callback.answer(t(lang, "seller.not_owner"), show_alert=True)
        return
    await state.set_state(PriceEdit.value)
    await state.update_data(account_id=account["id"])
    if callback.message is not None:
        await callback.message.answer(
            t(
                lang,
                "seller.edit_price_prompt",
                id=account.get("display_id") or account["id"],
                price=fmt_sum(account["price"]),
                min_price=fmt_sum(MIN_PRICE),
            ),
            reply_markup=cancel_reply(lang),
        )
    await callback.answer()


@router.message(StateFilter(PriceEdit.value))
async def price_update(
    message: Message, user: dict, lang: str, state: FSMContext
) -> None:
    price = parse_amount(message.text)
    if price is None or price < MIN_PRICE:
        await message.answer(t(lang, "seller.price_invalid", min_price=fmt_sum(MIN_PRICE)))
        return
    data = await state.get_data()
    account_id = data.get("account_id")
    if not isinstance(account_id, int):
        await state.clear()
        await message.answer(t(lang, "common.invalid_data"))
        return
    try:
        await marketplace.update_account_price(account_id, user["id"], price)
    except ServiceError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(t(lang, "seller.price_updated", price=fmt_sum(price)))
    await render_seller_panel(message, user, lang)


@router.callback_query(F.data == "seller:sales")
async def sales(callback: CallbackQuery, user: dict, lang: str) -> None:
    if not user["is_seller"]:
        await callback.answer(t(lang, "seller.only_sellers"), show_alert=True)
        return
    orders = await marketplace.list_seller_sales(user["id"])
    if not orders:

        await callback.answer()
        await respond(callback, t(lang, "seller.no_sales"), back_keyboard(lang, "seller:panel"))
        return
    lines = [
        t(
            lang,
            "seller.sales_line",
            id=order["id"],
            status=t(lang, "order.status." + order["status"]),
            total=fmt_sum(int(order["price"]) + int(order["penalty"])),
            penalty=fmt_sum(order["penalty"]),
            buyer=esc(order["buyer_name"]) or "—",
            date=fmt_dt(order["created_at"]),
        )
        for order in orders
    ]
    text = t(lang, "seller.sales_title") + "\n\n" + "\n".join(lines)

    await callback.answer()
    await respond(callback, text, back_keyboard(lang, "seller:panel"))


@router.callback_query(F.data == "seller:withdraw")
async def withdraw_start(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    if not user["is_seller"]:
        await callback.answer(t(lang, "seller.only_sellers"), show_alert=True)
        return
    if user["test_mode"]:
        await callback.answer(t(lang, "wallet.test_mode_no_withdraw"), show_alert=True)
        return
    await state.set_state(WithdrawFlow.amount)
    if callback.message is not None:
        await callback.message.answer(
            t(
                lang,
                "seller.withdraw_title",
                balance=fmt_sum(user["balance"]),
                min=fmt_sum(MIN_WITHDRAW),
            ),
            reply_markup=cancel_reply(lang),
        )
    await callback.answer()


@router.message(StateFilter(WithdrawFlow.amount))
async def withdraw_amount(message: Message, user: dict, lang: str, state: FSMContext) -> None:
    amount = parse_amount(message.text)
    if amount is None or amount < MIN_WITHDRAW:
        await message.answer(t(lang, "seller.withdraw_min", min=fmt_sum(MIN_WITHDRAW)))
        return
    if amount > int(user["balance"]):
        await message.answer(
            t(lang, "seller.withdraw_insufficient", balance=fmt_sum(user["balance"]))
        )
        return
    await state.update_data(amount=amount)
    await state.set_state(WithdrawFlow.card)
    await message.answer(t(lang, "seller.withdraw_card_prompt"), reply_markup=cancel_reply(lang))


@router.message(StateFilter(WithdrawFlow.card))
async def withdraw_card(
    message: Message, user: dict, lang: str, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    amount = int(data.get("amount", 0))
    try:
        withdrawal = await payment.create_withdrawal(user["id"], amount, message.text or "")
    except ServiceError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Xabarni o'chirishda xatolik: %s", exc)
    await message.answer(
        t(
            lang,
            "seller.withdraw_created",
            id=withdrawal["id"],
            amount=fmt_sum(withdrawal["amount"]),
            card=withdrawal["card_number"],
        ),
        reply_markup=main_menu_reply(lang, bool(user["is_seller"]), is_admin_user(user)),
    )
    info = dict(withdrawal)
    info.update(
        user_name=user["full_name"],
        user_tg=user["tg_id"],
        seller_code=user["seller_code"],
    )
    await notifications.withdrawal_created_admins(bot, info)
