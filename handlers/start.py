"""Start, help and language selection."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import CallbackQuery, Message

from config import (
    AUTO_COMPLETE_MINUTES,
    CHECK_MINUTES,
    COMMISSION_PERCENT,
    DEFAULT_LANG,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
    SELLER_SHARE_PERCENT,
    SUPPORT_USERNAME,
    SUPPORTED_LANGS,
)
from handlers.helpers import is_admin_user
from keyboards.inline import language_keyboard, terms_keyboard
from keyboards.reply import main_menu_reply, phone_reply
from services.errors import ServiceError
from services.i18n import t
from services.users import accept_terms, get_user, set_lang, set_phone

router = Router(name="start")


def _footer(lang: str) -> str:
    if SUPPORT_USERNAME:
        return t(lang, "start.help_footer", support=SUPPORT_USERNAME)
    return t(lang, "start.help_footer_none")


async def _send_onboarding(message: Message, user: dict, lang: str) -> bool:
    if is_admin_user(user):
        return False
    if not user["phone_verified"]:
        await message.answer(
            t(lang, "reg.phone_prompt"), reply_markup=phone_reply(lang)
        )
        return True
    if not user["terms_accepted"]:
        await message.answer(
            t(lang, "reg.terms_title", commission=COMMISSION_PERCENT),
            reply_markup=terms_keyboard(lang),
        )
        return True
    return False


@router.message(CommandStart())
async def cmd_start(message: Message, user: dict, lang: str) -> None:
    if not user["lang"]:
        await message.answer(
            t(DEFAULT_LANG, "lang.title"),
            reply_markup=language_keyboard(),
        )
        return
    if await _send_onboarding(message, user, lang):
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


@router.message(StateFilter(None), F.contact)
async def registration_contact(message: Message, user: dict, lang: str) -> None:
    if is_admin_user(user) or user["phone_verified"]:
        return
    contact = message.contact
    if contact is None:
        return
    if contact.user_id is not None and contact.user_id != message.from_user.id:
        await message.answer(t(lang, "reg.phone_invalid"))
        return
    digits = "".join(ch for ch in (contact.phone_number or "") if ch.isdigit())
    if len(digits) < 9:
        await message.answer(t(lang, "reg.phone_invalid"))
        return
    try:
        await set_phone(user["id"], "+" + digits)
    except ServiceError as exc:
        await message.answer(str(exc))
        return
    fresh = await get_user(user["id"])
    if fresh is None:
        return
    await message.answer(t(lang, "reg.phone_saved"))
    if not fresh["terms_accepted"]:
        await message.answer(
            t(lang, "reg.terms_title", commission=COMMISSION_PERCENT),
            reply_markup=terms_keyboard(lang),
        )
        return
    await message.answer(
        t(lang, "reg.completed"),
        reply_markup=main_menu_reply(
            lang, bool(fresh["is_seller"]), is_admin_user(fresh)
        ),
    )


@router.callback_query(F.data == "terms:accept")
async def terms_accept(callback: CallbackQuery, user: dict, lang: str) -> None:
    await accept_terms(user["id"])
    fresh = await get_user(user["id"])
    if fresh is None:
        await callback.answer(t(lang, "common.error"), show_alert=True)
        return
    await callback.answer(t(lang, "reg.completed"))
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            t(lang, "menu.title"),
            reply_markup=main_menu_reply(
                lang, bool(fresh["is_seller"]), is_admin_user(fresh)
            ),
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
        if await _send_onboarding(callback.message, fresh, new_lang):
            return
        await callback.message.answer(
            t(
                new_lang,
                "start.rules",
                commission=COMMISSION_PERCENT,
                share=SELLER_SHARE_PERCENT,
                check=CHECK_MINUTES,
                interval=PENALTY_INTERVAL_MINUTES,
                penalty=PENALTY_PERCENT,
                auto=AUTO_COMPLETE_MINUTES,
            )
        )
        await callback.message.answer(
            t(new_lang, "menu.title"),
            reply_markup=main_menu_reply(
                new_lang, bool(fresh["is_seller"]), is_admin_user(fresh)
            ),
        )
