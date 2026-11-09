"""Shared handler helpers."""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import SUPPORTED_LANGS
from services.i18n import t

logger = logging.getLogger(__name__)

MENU_ACTIONS = {
    "menu.browse": "browse",
    "menu.search": "search",
    "menu.wallet": "wallet",
    "menu.purchases": "purchases",
    "menu.profile": "profile",
    "menu.settings": "settings",
    "menu.rules": "rules",
    "menu.support": "support",
    "menu.seller_panel": "seller",
    "menu.become_seller": "become_seller",
    "menu.admin": "admin",
}


def lang_of(data: dict) -> str:
    return data.get("lang") or "uz"


def user_of(data: dict) -> dict:
    return data["user"]


def is_admin_user(user: dict) -> bool:
    return bool(user.get("is_admin"))


async def safe_edit(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.debug("edit_text xatosi: %s", exc)


async def respond(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(event, CallbackQuery):
        if event.message is not None:
            await safe_edit(event.message, text, reply_markup)
        else:
            await event.bot.send_message(
                event.from_user.id,
                text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    else:
        await event.answer(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def resolve_menu_action(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    for key, action in MENU_ACTIONS.items():
        for lang in SUPPORTED_LANGS:
            if text == t(lang, key):
                return action
    return None


class CancelFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        text = message.text.strip()
        return any(text == t(lang, "common.cancel") for lang in SUPPORTED_LANGS)
