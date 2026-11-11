"""Background scheduler for order reminders, penalties and auto-completion."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from services import marketplace, notifications

logger = logging.getLogger(__name__)

TICK_SECONDS = 15


async def _loop(bot: Bot) -> None:
    logger.info("Scheduler ishga tushdi (har %s soniya)", TICK_SECONDS)
    while True:
        try:
            # 1. Check and expire unpaid escrow orders (5-minute timer)
            expired = await marketplace.expire_pending_orders(bot)
            for item in expired:
                if item.get("buyer"):
                    await notifications.notify(
                        bot,
                        item["buyer"]["tg_id"],
                        item["buyer"].get("lang"),
                        "shop.order_cancelled_timeout",
                        order=item["order"]["id"],
                    )
                if item.get("seller"):
                    await notifications.notify(
                        bot,
                        item["seller"]["tg_id"],
                        item["seller"].get("lang"),
                        "shop.seller_order_cancelled_timeout",
                        order=item["order"]["id"],
                        title=item["account"]["title"],
                    )
                await notifications.notify_admin_event(
                    bot,
                    "order_cancelled",
                    order_id=item["order"]["id"],
                )

            # 2. Check unresponsive seller dispute timeouts (15 minutes)
            timeouts = await marketplace.check_dispute_timeouts(bot)
            for res in timeouts:
                if res.get("buyer_tg"):
                    await notifications.notify(
                        bot,
                        res["buyer_tg"],
                        res.get("buyer_lang"),
                        "notify.dispute_buyer_refund",
                        order=res["order_id"],
                        amount=res.get("total", 0),
                    )
                if res.get("seller_tg"):
                    await notifications.notify(
                        bot,
                        res["seller_tg"],
                        res.get("seller_lang"),
                        "notify.dispute_seller_timeout",
                        order=res["order_id"],
                    )
                await notifications.notify_admin_event(
                    bot,
                    "fraud_alert",
                    order_id=res["order_id"],
                    reason="Sotuvchi 15 daqiqada javob bermadi",
                    decision="Xaridorga avto-refund",
                )

            # 3. Check inspection deadlines and penalties
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
