"""Loads (and registers) the database user for every update."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import COMMISSION_PERCENT
from keyboards.inline import terms_keyboard
from keyboards.reply import phone_reply
from services import notifications
from services.i18n import t
from services.users import ensure_user, get_user_by_tg, user_lang
from utils import esc

_ONBOARDING_CALLBACKS = ("lang:", "terms:")
_ONBOARDING_COMMANDS = ("/start", "/help", "/cancel")


def _onboarding_allowed(event: TelegramObject) -> bool:
    if isinstance(event, CallbackQuery):
        data = event.data or ""
        return data == "noop" or data.startswith(_ONBOARDING_CALLBACKS)
    if isinstance(event, Message):
        text = (event.text or "").strip()
        if text:
            command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
            if command in _ONBOARDING_COMMANDS:
                return True
        return event.contact is not None
    return False


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        existing = await get_user_by_tg(tg_user.id)
        db_user = await ensure_user(tg_user.id, tg_user.full_name, tg_user.username)
        lang = user_lang(db_user)
        data["user"] = db_user
        data["lang"] = lang
        if existing is None:
            bot = data.get("bot")
            if bot is not None:
                await notifications.notify_admin_event(
                    bot,
                    "new_user",
                    name=esc(tg_user.full_name) or "—",
                    tg_id=tg_user.id,
                    phone=esc(db_user.get("phone") or "—"),
                )

        if db_user["is_blocked"] and not db_user["is_admin"]:
            text = t(lang, "common.access_denied")
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None

        if (
            not db_user["is_admin"]
            and not db_user["terms_accepted"]
            and not _onboarding_allowed(event)
        ):
            if isinstance(event, Message):
                if not db_user["phone_verified"]:
                    await event.answer(
                        t(lang, "reg.phone_prompt"),
                        reply_markup=phone_reply(lang),
                    )
                else:
                    await event.answer(
                        t(lang, "reg.terms_title", commission=COMMISSION_PERCENT),
                        reply_markup=terms_keyboard(lang),
                    )
            elif isinstance(event, CallbackQuery):
                key = (
                    "reg.need_terms"
                    if db_user["phone_verified"]
                    else "reg.need_phone"
                )
                await event.answer(t(lang, key), show_alert=True)
            return None

        return await handler(event, data)
