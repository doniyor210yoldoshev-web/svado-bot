"""Start, help and language selection."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from config import COMMISSION_PERCENT, DEFAULT_LANG, SUPPORT_USERNAME, SUPPORTED_LANGS
from handlers.helpers import is_admin_user
from keyboards.inline import language_keyboard
from keyboards.reply import main_menu_reply
from services.i18n import t
from services.users import get_user, set_lang

router = Router(name="start")


def _footer(lang: str) -> str:
    if SUPPORT_USERNAME:
        return t(lang, "start.help_footer", support=SUPPORT_USERNAME)
    return t(lang, "start.help_footer_none")


@router.message(CommandStart())
async def cmd_start(message: Message, user: dict, lang: str) -> None:
    if not user["lang"]:
        await message.answer(
            t(DEFAULT_LANG, "lang.title"),
            reply_markup=language_keyboard(),
        )
        return
    text = (
        t(lang, "start.warning", commission=COMMISSION_PERCENT)
        + "\n\n"
        + t(lang, "menu.title")
    )
    await message.answer(
        text,
        reply_markup=main_menu_reply(lang, bool(user["is_seller"]), is_admin_user(user)),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str) -> None:
    text = t(lang, "start.help", footer=_footer(lang))
    await message.answer(text)


@router.callback_query(F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery, user: dict) -> None:
    new_lang = (callback.data or "").split(":", 1)[1]
    if new_lang not in SUPPORTED_LANGS:
        await callback.answer(t(DEFAULT_LANG, "common.error"), show_alert=True)
        return
    await set_lang(user["id"], new_lang)
    fresh = await get_user(user["id"])
    if fresh is None:
        await callback.answer(t(new_lang, "common.error"), show_alert=True)
        return
    await callback.answer(t(new_lang, "lang.saved"))
    if callback.message is not None:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            t(new_lang, "menu.title"),
            reply_markup=main_menu_reply(
                new_lang, bool(fresh["is_seller"]), is_admin_user(fresh)
            ),
        )
