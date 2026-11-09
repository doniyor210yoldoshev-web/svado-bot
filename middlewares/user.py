"""Loads (and registers) the database user for every update."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from services.i18n import t
from services.users import ensure_user, user_lang


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

        db_user = await ensure_user(tg_user.id, tg_user.full_name, tg_user.username)
        lang = user_lang(db_user)
        data["user"] = db_user
        data["lang"] = lang

        if db_user["is_blocked"] and not db_user["is_admin"]:
            text = t(lang, "common.access_denied")
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None

        return await handler(event, data)
