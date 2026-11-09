"""Reply-menu text dispatcher and unknown-message fallback."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers.helpers import is_admin_user, resolve_menu_action
from handlers.views import (
    render_main_menu,
    render_orders,
    render_profile,
    render_rules,
    render_seller_panel,
    render_seller_register,
    render_settings,
    render_shop,
    render_support,
    render_wallet,
)
from keyboards.inline import admin_menu_keyboard
from keyboards.reply import cancel_reply
from services.i18n import t
from states import SearchFlow, SellerFlow

menu_router = Router(name="menu")
fallback_router = Router(name="fallback")


class MenuTextFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        return resolve_menu_action(message.text) is not None


@menu_router.message(MenuTextFilter())
async def dispatch_menu(
    message: Message, user: dict, lang: str, state: FSMContext
) -> None:
    action = resolve_menu_action(message.text)
    if action is None:
        return
    if action == "browse":
        await render_shop(message, user, lang, 1)
    elif action == "search":
        await state.set_state(SearchFlow.query)
        await message.answer(
            t(lang, "search.title") + "\n\n" + t(lang, "search.prompt"),
            reply_markup=cancel_reply(lang),
        )
    elif action == "wallet":
        await render_wallet(message, user, lang)
    elif action == "purchases":
        await render_orders(message, user, lang, 1)
    elif action == "profile":
        await render_profile(message, user, lang)
    elif action == "settings":
        await render_settings(message, user, lang)
    elif action == "rules":
        await render_rules(message, lang)
    elif action == "support":
        await render_support(message, lang)
    elif action == "seller":
        await render_seller_panel(message, user, lang)
    elif action == "become_seller":
        if user["is_seller"]:
            await render_seller_panel(message, user, lang)
        else:
            await state.set_state(SellerFlow.phone)
            await render_seller_register(message, user, lang)
    elif action == "admin":
        if is_admin_user(user):
            await message.answer(
                t(lang, "admin.title"), reply_markup=admin_menu_keyboard(lang)
            )
        else:
            await message.answer(t(lang, "admin.only_admin"))
    else:
        await render_main_menu(message, user, lang)


@fallback_router.message(F.text)
async def unknown_text(message: Message, lang: str) -> None:
    await message.answer(t(lang, "common.error"))


@fallback_router.message()
async def unknown_other(message: Message, lang: str) -> None:
    await message.answer(t(lang, "common.error"))
