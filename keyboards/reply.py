"""Reply keyboards used during text/contact input."""

from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from services.i18n import t


def main_menu_reply(lang: str, is_seller: bool, is_admin: bool) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=t(lang, "menu.browse"))
    kb.button(text=t(lang, "menu.search"))
    kb.button(text=t(lang, "menu.wallet"))
    kb.button(text=t(lang, "menu.purchases"))
    kb.button(text=t(lang, "menu.profile"))
    kb.button(text=t(lang, "menu.settings"))
    if is_seller:
        kb.button(text=t(lang, "menu.seller_panel"))
    else:
        kb.button(text=t(lang, "menu.become_seller"))
    kb.button(text=t(lang, "menu.rules"))
    kb.button(text=t(lang, "menu.support"))
    if is_admin:
        kb.button(text=t(lang, "menu.admin"))
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


def cancel_reply(lang: str) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=t(lang, "common.cancel"))
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)


def skip_reply(lang: str, text: str) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=text)
    kb.button(text=t(lang, "common.cancel"))
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


def phone_reply(lang: str) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=t(lang, "seller.phone_btn"), request_contact=True)
    kb.button(text=t(lang, "common.cancel"))
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)
