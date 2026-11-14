"""Shared handler helpers."""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message

from config import SUPPORTED_LANGS
from services.i18n import t

logger = logging.getLogger(__name__)

MENU_ACTIONS = {
    "menu.browse": "browse",
    "menu.sell": "sell",
    "menu.search": "search",
    "menu.my_accounts": "my_accounts",
    "menu.my_orders": "my_orders",
    "menu.wallet": "wallet",
    "menu.history": "history",
    "menu.settings": "settings",
    "menu.help": "help",
    "menu.admin": "admin",
    # Aliases (inline keyboards and older locale keys)
    "menu.seller_panel": "my_accounts",
    "menu.become_seller": "sell",
    "menu.purchases": "my_orders",
    "menu.rules": "help",
    "menu.support": "help",
    "menu.profile": "settings",
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
        if "message is not modified" in str(exc):
            return
        logger.debug("edit_text xatosi: %s", exc)
        if message.photo or message.document or message.video:
            try:
                await message.delete()
            except TelegramAPIError:
                pass
            try:
                await message.answer(
                    text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except TelegramAPIError as send_exc:
                logger.warning("Xabar yuborilmadi: %s", send_exc)


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


async def respond_photo(
    event: Message | CallbackQuery,
    photo: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Send or edit a photo message. Returns False when Telegram rejects it."""
    try:
        if isinstance(event, CallbackQuery):
            if event.message is not None:
                try:
                    await event.message.edit_media(
                        InputMediaPhoto(
                            media=photo,
                            caption=caption,
                            parse_mode="HTML",
                        ),
                        reply_markup=reply_markup,
                    )
                    return True
                except TelegramBadRequest:
                    pass
            await event.bot.send_photo(
                event.from_user.id,
                photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            if event.message is not None:
                try:
                    await event.message.delete()
                except TelegramAPIError:
                    pass
        else:
            await event.answer_photo(
                photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        return True
    except TelegramAPIError as exc:
        logger.warning("Rasm yuborilmadi: %s", exc)
        return False


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
