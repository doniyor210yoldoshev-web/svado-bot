"""Reusable rendering helpers for screens."""

from __future__ import annotations

import math

from aiogram.types import CallbackQuery, Message

from config import (
    AUTO_COMPLETE_MINUTES,
    CHECK_MINUTES,
    COMMISSION_PERCENT,
    PAGE_SIZE,
    PAYMENT_PROVIDER,
    PENALTY_INTERVAL_MINUTES,
    PENALTY_PERCENT,
    SELLER_SHARE_PERCENT,
    SUPPORT_USERNAME,
)
from handlers.helpers import is_admin_user, respond, respond_photo
from keyboards.inline import (
    back_keyboard,
    main_menu,
    order_detail_keyboard,
    order_list_keyboard,
    seller_accounts_keyboard,
    seller_panel_keyboard,
    settings_keyboard,
    shop_detail_keyboard,
    shop_keyboard,
    wallet_keyboard,
)
from keyboards.reply import main_menu_reply, phone_reply
from services import hamyon as hamyon_service
from services import marketplace, payment
from services.i18n import t
from services.users import get_user, seller_rating, seller_stats
from utils import esc, fmt_dt, fmt_sum, parse_dt, utcnow


def _paginate(items: list[dict], page: int) -> tuple[list[dict], int, int]:
    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    page = min(max(1, page), total_pages)
    start = (page - 1) * PAGE_SIZE
    return items[start : start + PAGE_SIZE], page, total_pages


def _balance(user: dict) -> int:
    return int(user["test_balance"] if user["test_mode"] else user["balance"])


def _external_escrow(user: dict) -> bool:
    return (
        not user["test_mode"]
        and PAYMENT_PROVIDER == "hamyon"
        and hamyon_service.is_configured()
    )


async def render_main_menu(event: Message | CallbackQuery, user: dict, lang: str) -> None:
    text = t(lang, "menu.title")
    if isinstance(event, Message):
        await event.answer(
            text,
            reply_markup=main_menu_reply(lang, bool(user["is_seller"]), is_admin_user(user)),
        )
    else:
        await respond(event, text, main_menu(lang, bool(user["is_seller"]), is_admin_user(user)))


async def render_profile(event: Message | CallbackQuery, user: dict, lang: str) -> None:
    stats = await seller_stats(user["id"])
    rating = await seller_rating(user["id"]) if user["is_seller"] else "—"
    role = (
        t(lang, "profile.role_admin")
        if is_admin_user(user)
        else t(lang, "profile.role_seller")
        if user["is_seller"]
        else t(lang, "profile.role_buyer")
    )
    lines = [
        t(lang, "profile.tg", value=user["tg_id"]),
        t(lang, "profile.name", value=esc(user["full_name"]) or "—"),
        t(
            lang,
            "profile.username",
            value=f"@{esc(user['username'])}" if user["username"] else "—",
        ),
        t(lang, "profile.phone", value=esc(user["phone"]) or "—"),
        t(lang, "profile.role", value=role),
        t(lang, "profile.balance", value=fmt_sum(user["balance"])),
    ]
    if user["test_mode"]:
        lines.append(t(lang, "profile.test_balance", value=fmt_sum(user["test_balance"])))
    if user["is_seller"]:
        lines.append(t(lang, "profile.seller_code", value=user["seller_code"]))
        lines.append(t(lang, "profile.rating", value=rating))
        lines.append(t(lang, "profile.active", value=stats["active"]))
        lines.append(t(lang, "profile.paused", value=stats["paused"]))
        lines.append(t(lang, "profile.sold", value=stats["sold"]))
    if user["is_verified"]:
        lines.append(t(lang, "profile.verified"))
    lines.append(t(lang, "profile.registered", value=fmt_dt(user["created_at"])))
    text = t(lang, "profile.title") + "\n\n" + "\n".join(lines)
    await respond(event, text, back_keyboard(lang))


async def render_settings(event: Message | CallbackQuery, user: dict, lang: str) -> None:
    await respond(
        event,
        t(lang, "settings.title"),
        settings_keyboard(lang, bool(user["test_mode"]), is_admin_user(user)),
    )


async def render_rules(event: Message | CallbackQuery, lang: str) -> None:
    text = t(
        lang,
        "start.rules",
        commission=COMMISSION_PERCENT,
        share=SELLER_SHARE_PERCENT,
        check=CHECK_MINUTES,
        interval=PENALTY_INTERVAL_MINUTES,
        penalty=PENALTY_PERCENT,
        auto=AUTO_COMPLETE_MINUTES,
    )
    await respond(event, text, back_keyboard(lang))


async def render_support(event: Message | CallbackQuery, lang: str) -> None:
    if SUPPORT_USERNAME:
        text = t(lang, "start.help_footer", support=SUPPORT_USERNAME)
    else:
        text = t(lang, "start.help_footer_none")
    await respond(event, text, back_keyboard(lang))


async def render_shop(
    event: Message | CallbackQuery, user: dict, lang: str, page: int
) -> None:
    mode = "test" if user["test_mode"] else "real"
    accounts, total = await marketplace.list_market_accounts(mode, page)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), total_pages)
    if not accounts:
        await respond(event, t(lang, "shop.empty"), back_keyboard(lang))
        return
    text = t(lang, "shop.title", page=page, count=total)
    if user["test_mode"]:
        text += "\n\n" + t(lang, "shop.test_mode_note")
    await respond(
        event,
        text,
        shop_keyboard(lang, accounts, page, total_pages, bool(user["test_mode"])),
    )


async def render_shop_detail(
    event: Message | CallbackQuery, user: dict, lang: str, account_id: int
) -> None:
    account = await marketplace.get_account(account_id)
    if account is None:
        await respond(event, t(lang, "common.not_found"), back_keyboard(lang, "menu:browse:1"))
        return
    seller = await get_user(account["seller_id"])
    if seller is None:
        await respond(event, t(lang, "common.not_found"), back_keyboard(lang, "menu:browse:1"))
        return
    rating = await seller_rating(seller["id"])
    mode = "test" if user["test_mode"] else "real"
    is_owner = account["seller_id"] == user["id"]
    external = _external_escrow(user)
    can_buy = (
        account["status"] == "active"
        and account["mode"] == mode
        and not is_owner
        and not seller["is_blocked"]
        and (external or _balance(user) >= int(account["price"]))
    )
    text = t(
        lang,
        "shop.detail",
        id=account.get("display_id") or account["id"],
        title=esc(account["title"]),
        description=esc(account["description"]) or t(lang, "seller.no_description"),
        seller=esc(seller["full_name"]) or "—",
        seller_code=esc(seller["seller_code"]) or "—",
        rating=rating,
        price=fmt_sum(account["price"]),
        balance=fmt_sum(_balance(user)),
    )
    if account["status"] != "active" or account["mode"] != mode:
        text += "\n\n" + t(lang, "shop.not_available")
    elif is_owner:
        text += "\n\n" + t(lang, "shop.own_account")
    elif not external and _balance(user) < int(account["price"]):
        text += "\n\n" + t(
            lang,
            "shop.buy_insufficient",
            required=fmt_sum(account["price"]),
            available=fmt_sum(_balance(user)),
        )
    keyboard = shop_detail_keyboard(lang, account, can_buy, is_owner)
    photo_id = account.get("photo_id")
    if photo_id and len(text) <= 1024 and await respond_photo(
        event, photo_id, text, keyboard
    ):
        return
    await respond(event, text, keyboard)


async def render_orders(
    event: Message | CallbackQuery, user: dict, lang: str, page: int
) -> None:
    orders = await marketplace.list_buyer_orders(user["id"], limit=100)
    if not orders:
        await respond(event, t(lang, "order.no_history"), back_keyboard(lang))
        return
    chunk, page, total_pages = _paginate(orders, page)
    lines = [
        t(
            lang,
            "order.history_line",
            id=order["id"],
            status=t(lang, "order.status." + order["status"]),
            total=fmt_sum(int(order["price"]) + int(order["penalty"])),
            date=fmt_dt(order["created_at"]),
        )
        for order in chunk
    ]
    text = t(lang, "order.history_title") + "\n\n" + "\n".join(lines)
    await respond(event, text, order_list_keyboard(lang, chunk, page, total_pages))


async def render_wallet(event: Message | CallbackQuery, user: dict, lang: str) -> None:
    summary = await payment.wallet_summary(user)
    test = (
        t(lang, "wallet.test_line", value=fmt_sum(summary["test_balance"]))
        if summary["test_mode"]
        else ""
    )
    # Jami kirim/chiqim qo'shildi
    extra_lines = []
    if not summary["test_mode"]:
        total_in = summary.get("total_in", 0)
        total_out = summary.get("total_out", 0)
        if total_in > 0 or total_out > 0:
            extra_lines.append(
                t(lang, "wallet.total_in", value=fmt_sum(total_in))
            )
            extra_lines.append(
                t(lang, "wallet.total_out", value=fmt_sum(total_out))
            )
        pending_topups = summary.get("pending_topups", 0)
        if pending_topups > 0:
            extra_lines.append(
                t(lang, "wallet.pending_in", value=fmt_sum(pending_topups))
            )
        tx_count = summary.get("tx_count", 0)
        if tx_count > 0:
            extra_lines.append(t(lang, "wallet.tx_count", count=tx_count))
    extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
    text = t(
        lang,
        "wallet.title",
        balance=fmt_sum(summary["balance"]),
        test=test,
        commission=COMMISSION_PERCENT,
    ) + extra
    await respond(event, text, wallet_keyboard(lang, summary["test_mode"]))


async def render_history(event: Message | CallbackQuery, user: dict, lang: str) -> None:
    mode = "test" if user["test_mode"] else "real"
    rows = await payment.list_transactions(user["id"], mode)
    if not rows:
        await respond(event, t(lang, "wallet.no_transactions"), back_keyboard(lang))
        return
    lines = []
    for row in rows:
        sign = "+" if int(row["amount"]) > 0 else "-"
        label = t(lang, f"tx.type.{row['type']}")
        lines.append(
            t(
                lang,
                "wallet.tx_line",
                label=label,
                sign=sign,
                amount=fmt_sum(abs(int(row["amount"]))),
                date=fmt_dt(row["created_at"]),
            )
        )
    text = t(lang, "wallet.history") + "\n\n" + "\n".join(lines)
    await respond(event, text, back_keyboard(lang))


async def render_seller_panel(event: Message | CallbackQuery, user: dict, lang: str) -> None:
    if not user["is_seller"]:
        await respond(event, t(lang, "seller.not_seller"), back_keyboard(lang))
        return
    stats = await seller_stats(user["id"])
    rating = await seller_rating(user["id"])
    text = t(
        lang,
        "seller.panel_title",
        code=esc(user["seller_code"]),
        balance=fmt_sum(user["balance"]),
        active=stats["active"],
        paused=stats["paused"],
        sold=stats["sold"],
        rating=rating,
    )
    await respond(event, text, seller_panel_keyboard(lang))


async def render_seller_accounts(
    event: Message | CallbackQuery, user: dict, lang: str, page: int
) -> None:
    if not user["is_seller"]:
        await respond(event, t(lang, "seller.not_seller"), back_keyboard(lang))
        return
    accounts = await marketplace.list_seller_accounts(user["id"])
    if not accounts:
        await respond(event, t(lang, "seller.no_accounts"), seller_panel_keyboard(lang))
        return
    chunk, page, total_pages = _paginate(accounts, page)
    text = t(lang, "seller.accounts_title", count=len(accounts))
    await respond(event, text, seller_accounts_keyboard(lang, chunk, page, total_pages))


async def render_seller_register(
    event: Message | CallbackQuery, user: dict, lang: str
) -> None:
    if user["is_seller"]:
        await respond(event, t(lang, "seller.already_seller"), back_keyboard(lang))
        return
    text = t(lang, "seller.register_title")
    if isinstance(event, Message):
        await event.answer(text, reply_markup=phone_reply(lang))
    else:
        if event.message is not None:
            await event.message.answer(text, reply_markup=phone_reply(lang))
        await event.answer()


async def render_order_detail(
    event: Message | CallbackQuery, user: dict, lang: str, order_id: int
) -> None:
    order = await marketplace.get_order_with_people(order_id)
    if order is None:
        await respond(event, t(lang, "order.not_found"), back_keyboard(lang, "order:page:1"))
        return
    if order["buyer_id"] != user["id"]:
        await respond(event, t(lang, "order.not_owner"), back_keyboard(lang, "order:page:1"))
        return
    text = t(
        lang,
        "order.view",
        id=order["id"],
        account=order.get("account_display_id") or order["account_id"],
        seller=esc(order["seller_name"]) or "—",
        seller_code=esc(order["seller_code"]) or "—",
        price=fmt_sum(order["price"]),
        penalty=fmt_sum(order["penalty"]),
        total=fmt_sum(int(order["price"]) + int(order["penalty"])),
        status=t(lang, "order.status." + order["status"]),
        created=fmt_dt(order["created_at"]),
    )
    if order["status"] == "checking":
        deadline = parse_dt(order["check_deadline"])
        if deadline is not None:
            remaining = (deadline - utcnow()).total_seconds()
            if remaining > 0:
                remaining_text = t(
                    lang,
                    "order.remaining",
                    minutes=int(remaining // 60),
                    seconds=int(remaining % 60),
                )
            else:
                remaining_text = t(
                    lang, "order.overdue", minutes=int(-remaining // 60)
                )
            text += "\n" + t(
                lang,
                "order.deadline",
                deadline=fmt_dt(order["check_deadline"]),
                remaining=remaining_text,
            )
    if order["completed_at"]:
        text += "\n" + t(
            lang, "order.completed_at", date=fmt_dt(order["completed_at"])
        )
    await respond(event, text, order_detail_keyboard(lang, order))
