"""Background scheduler for order reminders, penalties and auto-completion."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from services import marketplace, notifications

logger = logging.getLogger(__name__)

TICK_SECONDS = 30


async def _loop(bot: Bot) -> None:
    logger.info("Scheduler ishga tushdi (har %s soniya)", TICK_SECONDS)
    while True:
        try:
            events = await marketplace.accrue_penalties()
            for event in events:
                await notifications.notify(
                    bot,
                    event["tg"],
                    event.get("lang"),
                    event["key"],
                    **event.get("kwargs", {}),
                )
        except asyncio.CancelledError:
            logger.info("Scheduler to'xtatildi")
            raise
        except Exception:
            logger.exception("Scheduler tickida xatolik")
        await asyncio.sleep(TICK_SECONDS)


def start_scheduler(bot: Bot) -> asyncio.Task:
    return asyncio.create_task(_loop(bot), name="order-scheduler")


async def stop_scheduler(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
