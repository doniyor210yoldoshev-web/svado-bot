"""Simple in-memory anti-flood middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

MESSAGE_INTERVAL = 0.6
CALLBACK_INTERVAL = 0.4
MAX_ENTRIES = 20_000


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._last: dict[tuple[int, str], float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            kind, interval = "cb", CALLBACK_INTERVAL
        elif isinstance(event, Message):
            kind, interval = "msg", MESSAGE_INTERVAL
        else:
            return await handler(event, data)

        key = (user.id, kind)
        now = time.monotonic()
        last = self._last.get(key, 0.0)
        if now - last < interval:
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None
        self._last[key] = now

        if len(self._last) > MAX_ENTRIES:
            threshold = now - 60
            self._last = {k: v for k, v in self._last.items() if v >= threshold}

        return await handler(event, data)
