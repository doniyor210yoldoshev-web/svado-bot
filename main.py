"""Entry point: wires database, bot, middlewares, routers and scheduler."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent

from config import (
    ADMIN_IDS,
    BOT_TOKEN,
    FSM_DB_PATH,
    SEED_TEST_ACCOUNTS,
    TEST_MAX_ACCOUNTS,
)
from database.db import db
from database.fsm import SQLiteStorage
from handlers import routers
from health import start_health_server
from middlewares import ThrottlingMiddleware, UserMiddleware
from services.i18n import reload_locales
from services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> bool:
    logger.exception("Update ishlashida xatolik: %s", event.exception)
    return True


async def main() -> None:
    if not BOT_TOKEN:
        logger.error(
            "BOT_TOKEN topilmadi. .env fayliga BotFather'dan olingan tokenni yozing."
        )
        return
    if not ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS bo'sh. Admin panel va to'lovlarni tasdiqlash ishlamaydi."
        )

    reload_locales()
    await db.connect()
    logger.info("Ma'lumotlar bazasi ulandi: %s", db.describe())

    if SEED_TEST_ACCOUNTS:
        try:
            from services import marketplace

            created = await marketplace.seed_test_accounts(
                TEST_MAX_ACCOUNTS, target_mode="test"
            )
            logger.info("Test e'lonlar tayyorlandi: %s ta yangi", created)
        except Exception as exc:  # noqa: BLE001 - seeding is non critical
            logger.warning("Test e'lonlarni tayyorlab bo'lmadi: %s", exc)

    storage = SQLiteStorage(FSM_DB_PATH)
    await storage.connect()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=storage)

    dispatcher.message.outer_middleware(ThrottlingMiddleware())
    dispatcher.callback_query.outer_middleware(ThrottlingMiddleware())
    dispatcher.message.outer_middleware(UserMiddleware())
    dispatcher.callback_query.outer_middleware(UserMiddleware())

    dispatcher.include_routers(*routers)
    dispatcher.errors.register(on_error)

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Asosiy menyu / Главное меню"),
                BotCommand(command="cancel", description="Bekor qilish / Отмена"),
                BotCommand(command="help", description="Yordam / Помощь"),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - commands are non critical
        logger.warning("Buyruqlarni o'rnatib bo'lmadi: %s", exc)

    health_server = await start_health_server(bot)
    scheduler_task = start_scheduler(bot)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Bot ishga tushdi")
        await dispatcher.start_polling(bot)
    finally:
        await stop_scheduler(scheduler_task)
        if health_server is not None:
            health_server.close()
            await health_server.wait_closed()
        await storage.close()
        await db.close()
        await bot.session.close()
        logger.info("Bot to'xtatildi")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
