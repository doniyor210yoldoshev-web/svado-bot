"""Shared menu, settings and cancellation handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import TEST_INITIAL_BALANCE
from handlers.helpers import CancelFilter, is_admin_user, respond
from handlers.views import (
    render_main_menu,
    render_profile,
    render_rules,
    render_settings,
    render_support,
)
from keyboards.inline import language_keyboard
from services import users as users_service
from services.i18n import t
from utils import fmt_sum

router = Router(name="common")


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, user: dict, lang: str) -> None:
    await callback.answer()
    await render_main_menu(callback, user, lang)


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery, user: dict, lang: str) -> None:
    await callback.answer()
    await render_profile(callback, user, lang)


@router.callback_query(F.data == "menu:rules")
async def menu_rules(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await render_rules(callback, lang)


@router.callback_query(F.data == "menu:support")
async def menu_support(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await render_support(callback, lang)


@router.callback_query(F.data == "menu:settings")
async def menu_settings(callback: CallbackQuery, user: dict, lang: str) -> None:
    await callback.answer()
    await render_settings(callback, user, lang)


@router.callback_query(F.data == "set:lang")
async def change_language(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await respond(callback, t(lang, "lang.title"), language_keyboard())


@router.callback_query(F.data == "set:test")
async def toggle_test(callback: CallbackQuery, user: dict, lang: str) -> None:
    if not is_admin_user(user):
        await callback.answer(t(lang, "admin.only_admin"), show_alert=True)
        return
    updated = await users_service.toggle_test_mode(user["id"], TEST_INITIAL_BALANCE)
    key = "admin.test_on_msg" if updated["test_mode"] else "admin.test_off_msg"
    await callback.answer(
        t(lang, key, balance=fmt_sum(updated["test_balance"])), show_alert=True
    )
    await render_settings(callback, updated, lang)


@router.message(Command("cancel"))
@router.message(CancelFilter())
async def cancel(message: Message, state: FSMContext, user: dict, lang: str) -> None:
    await state.clear()
    await message.answer(t(lang, "start.cancelled"))
    await render_main_menu(message, user, lang)
