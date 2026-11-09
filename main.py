import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import ADMIN_IDS, BOT_TOKEN, FSM_DB_PATH
from db import db
from handlers import admin, buyer, seller, start, wallet
from handlers.common import error_handler
from middlewares import ThrottlingMiddleware, UserMiddleware
from scheduler import penalty_loop
from sqlite_storage import SQLiteStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def start_health_server() -> asyncio.AbstractServer | None:
    port_raw = os.getenv("PORT")
    if not port_raw:
        return None
    port = int(port_raw)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(1024)
            body = b"OK"
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    logger.info("Health server %s-portda ishga tushdi", port)
    return server


async def main() -> None:
    if not BOT_TOKEN:
        logger.error(
            "BOT_TOKEN topilmadi. .env faylini ochib, BotFather'dan olingan "
            "tokenni BOT_TOKEN qatoriga yozing."
        )
        return
    if not ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS bo'sh. Admin panel ishlamaydi va to'lovlar tasdiqlanmaydi. "
            ".env fayliga admin Telegram ID sini yozing."
        )

    await db.connect()
    logger.info("Ma'lumotlar bazasi ulandi: %s", db.path)

    storage = SQLiteStorage(FSM_DB_PATH)
    await storage.connect()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=storage)

    dispatcher.message.middleware(ThrottlingMiddleware())
    dispatcher.callback_query.middleware(ThrottlingMiddleware())
    dispatcher.message.middleware(UserMiddleware())
    dispatcher.callback_query.middleware(UserMiddleware())

    dispatcher.include_routers(
        start.router,
        seller.router,
        buyer.router,
        wallet.router,
        admin.router,
    )
    dispatcher.errors.register(error_handler)

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Asosiy menyu"),
                BotCommand(command="cancel", description="Amalni bekor qilish"),
                BotCommand(command="help", description="Yordam"),
            ]
        )
    except Exception as exc:
        logger.warning("Buyruqlarni o'rnatib bo'lmadi: %s", exc)

    health_server = await start_health_server()
    scheduler_task = asyncio.create_task(penalty_loop(bot))
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Bot ishga tushdi")
        await dispatcher.start_polling(bot)
    finally:
        scheduler_task.cancel()
        if health_server is not None:
            health_server.close()
            await health_server.wait_closed()
        try:
            await scheduler_task
        except (asyncio.CancelledError, Exception):
            pass
        await storage.close()
        await db.close()
        await bot.session.close()
        logger.info("Bot to'xtatildi")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
