"""Search by seller ID or account ID."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.views import render_shop_detail
from keyboards.reply import cancel_reply
from services import marketplace
from services.i18n import t
from services.users import seller_rating
from states import SearchFlow
from utils import esc, fmt_dt, fmt_sum

router = Router(name="search")


@router.callback_query(F.data == "menu:search")
async def search_start(
    callback: CallbackQuery, user: dict, lang: str, state: FSMContext
) -> None:
    await state.set_state(SearchFlow.query)
    if callback.message is not None:
        await callback.message.answer(
            t(lang, "search.title") + "\n\n" + t(lang, "search.prompt"),
            reply_markup=cancel_reply(lang),
        )
    await callback.answer()


@router.message(StateFilter(SearchFlow.query))
async def search_query(
    message: Message, user: dict, lang: str, state: FSMContext
) -> None:
    query = (message.text or "").strip()
    if not query.isdigit():
        await message.answer(t(lang, "search.invalid"))
        return
    mode = "test" if user["test_mode"] else "real"
    found = await marketplace.search_by_id(query, mode)
    if found is None:
        await state.clear()
        await message.answer(t(lang, "search.not_found"))
        return
    await state.clear()

    if found["type"] == "account":
        await render_shop_detail(message, user, lang, found["account"]["id"])
        return

    seller = found["seller"]
    rating = await seller_rating(seller["id"])
    count = await marketplace.count_active_accounts(seller["id"], mode)
    verified = t(lang, "search.verified") if seller["is_verified"] else ""
    text = t(
        lang,
        "search.seller",
        name=esc(seller["full_name"]) or "—",
        code=esc(seller["seller_code"]) or "—",
        rating=rating,
        count=count,
        verified=verified,
        date=fmt_dt(seller["created_at"]),
    )
    accounts = await marketplace.list_active_accounts(seller["id"], mode)
    if not accounts:
        text += "\n\n" + t(lang, "search.no_products")

    kb = InlineKeyboardBuilder()
    for account in accounts:
        kb.row(
            InlineKeyboardButton(
                text=f"#{account['id']} · {account['title'][:26]} · {fmt_sum(account['price'])}",
                callback_data=f"shop:view:{account['id']}",
            )
        )
    kb.row(
        InlineKeyboardButton(text=t(lang, "common.back"), callback_data="menu:home")
    )
    await message.answer(text, reply_markup=kb.as_markup())
