"""Outgoing Telegram notifications with per-user localization."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import SELLER_SHARE_PERCENT
from services import users as users_service
from services.i18n import t
from utils import fmt_dt, fmt_sum

logger = logging.getLogger(__name__)


async def send(
    bot: Bot,
    tg_id: int,
    text: str,
    reply_markup=None,
) -> bool:
    if not tg_id:
        return False
    try:
        await bot.send_message(
            tg_id,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except TelegramAPIError as exc:
        logger.warning("Xabar yuborilmadi (tg_id=%s): %s", tg_id, exc)
        return False


async def notify(
    bot: Bot,
    tg_id: int,
    lang: str | None,
    key: str,
    reply_markup=None,
    **kwargs,
) -> bool:
    return await send(bot, tg_id, t(lang, key, **kwargs), reply_markup=reply_markup)


async def notify_admins(bot: Bot, key: str, **kwargs) -> None:
    for admin_tg, lang in await users_service.admin_langs():
        await notify(bot, admin_tg, lang, key, **kwargs)


async def sale_pending(bot: Bot, order: dict, account: dict, seller: dict) -> None:
    await notify(
        bot,
        seller["tg_id"],
        seller["lang"],
        "notify.seller_sold",
        title=account["title"],
        price=fmt_sum(order["price"]),
        order=order["id"],
        share=SELLER_SHARE_PERCENT,
    )


async def sale_completed(bot: Bot, info: dict) -> None:
    if info.get("seller_tg"):
        await notify(
            bot,
            info["seller_tg"],
            info.get("seller_lang"),
            "notify.seller_completed",
            order=info["order_id"],
            share=fmt_sum(info["seller_share"]),
            percent=SELLER_SHARE_PERCENT,
            commission=fmt_sum(info["commission"]),
        )


async def dispute_opened(bot: Bot, dispute: dict) -> None:
    if dispute.get("seller_tg"):
        await notify(
            bot,
            dispute["seller_tg"],
            dispute.get("seller_lang"),
            "notify.dispute_seller",
            order=dispute["order_id"],
            account=dispute["account_id"],
            reason=dispute["reason"],
        )


async def dispute_resolved(bot: Bot, info: dict) -> None:
    if info["action"] == "refund":
        if info.get("buyer_tg"):
            await notify(
                bot,
                info["buyer_tg"],
                info.get("buyer_lang"),
                "notify.dispute_buyer_refund",
                order=info["order_id"],
                amount=fmt_sum(info["total"]),
            )
        if info.get("seller_tg"):
            await notify(
                bot,
                info["seller_tg"],
                info.get("seller_lang"),
                "notify.dispute_seller_refund",
                order=info["order_id"],
            )
        return
    if info.get("seller_tg"):
        await notify(
            bot,
            info["seller_tg"],
            info.get("seller_lang"),
            "notify.dispute_seller_release",
            order=info["order_id"],
            amount=fmt_sum(info["seller_share"]),
        )
    if info.get("buyer_tg"):
        await notify(
            bot,
            info["buyer_tg"],
            info.get("buyer_lang"),
            "notify.dispute_buyer_release",
            order=info["order_id"],
        )


async def topup_created_admins(bot: Bot, topup: dict) -> None:
    await notify_admins(
        bot,
        "admin.topup_card",
        name=topup.get("user_name") or "—",
        tg=topup.get("user_tg"),
        username=topup.get("username") or "—",
        amount=fmt_sum(topup["amount"]),
        receipt_type=topup.get("receipt_type") or "—",
        date=fmt_dt(topup.get("created_at")),
        id=topup["id"],
    )


async def topup_reviewed(bot: Bot, info: dict, approved: bool) -> None:
    if not info.get("user_tg"):
        return
    key = "wallet.topup_approved" if approved else "wallet.topup_rejected"
    await notify(
        bot,
        info["user_tg"],
        info.get("user_lang"),
        key,
        amount=fmt_sum(info["amount"]),
    )


async def withdrawal_created_admins(bot: Bot, withdrawal: dict) -> None:
    await notify_admins(
        bot,
        "admin.withdrawal_card",
        name=withdrawal.get("user_name") or "—",
        tg=withdrawal.get("user_tg"),
        code=withdrawal.get("seller_code") or "—",
        amount=fmt_sum(withdrawal["amount"]),
        card=withdrawal["card_number"],
        date=fmt_dt(withdrawal.get("created_at")),
        id=withdrawal["id"],
    )


async def withdrawal_reviewed(bot: Bot, info: dict, approved: bool) -> None:
    if not info.get("user_tg"):
        return
    key = "wallet.withdraw_approved" if approved else "wallet.withdraw_rejected"
    await notify(
        bot,
        info["user_tg"],
        info.get("user_lang"),
        key,
        amount=fmt_sum(info["amount"]),
    )


async def balance_changed(bot: Bot, user: dict, amount: int, new_balance: int) -> None:
    await notify(
        bot,
        user["tg_id"],
        user["lang"],
        "admin.balance_updated",
        amount=fmt_sum(amount),
        balance=fmt_sum(new_balance),
    )


async def notify_admin_event(bot: Bot, event_type: str, **kwargs) -> None:
    """Informational admin alerts (Section 15: strictly informational, no dispute actions)."""
    messages = {
        "new_user": "👤 <b>[ADMIN] Yangi foydalanuvchi:</b> {name} (ID: <code>{tg_id}</code>, Tel: {phone})",
        "new_seller": "🧑‍💼 <b>[ADMIN] Yangi sotuvchi:</b> {name} (ID: <code>{tg_id}</code>, Kod: <b>{code}</b>)",
        "new_listing": "📦 <b>[ADMIN] Yangi e'lon:</b> #{id} - <b>{title}</b> (Narxi: {price})",
        "new_order": "🧾 <b>[ADMIN] Yangi buyurtma yaratildi:</b> #{id} (Akkaunt: #{account_id}, Narx: {price})",
        "payment_confirmed": "💰 <b>[ADMIN] To'lov tasdiqlandi:</b> Buyurtma #{order_id} (Summa: {amount})",
        "trade_completed": "✅ <b>[ADMIN] Savdo muvaffaqiyatli yakunlandi:</b> #{order_id} (Sotuvchiga: {share}, Komissiya: {commission})",
        "fraud_alert": "⚠️ <b>[ADMIN OGOHLANTIRISH] Firibgarlik / Nizo:</b> #{order_id}\nSabab: {reason}\nQaror: {decision}",
        "order_cancelled": "ℹ️ <b>[ADMIN] Buyurtma bekor qilindi (5 daqiqa vaqt tugadi):</b> #{order_id}",
    }
    template = messages.get(event_type)
    if not template:
        return
    text = template.format(**kwargs)
    for admin_tg, _ in await users_service.admin_langs():
        await send(bot, admin_tg, text)
