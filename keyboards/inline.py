"""Inline and reply keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.i18n import t
from utils import fmt_sum


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def language_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🇺🇿 O'zbekcha", callback_data="lang:uz")
    kb.button(text="🇷🇺 Русский", callback_data="lang:ru")
    kb.button(text="🇬🇧 English", callback_data="lang:en")
    kb.adjust(1)
    return kb.as_markup()


def main_menu(lang: str, is_seller: bool, is_admin: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        _b(t(lang, "menu.browse"), "menu:browse:1"),
        _b(t(lang, "menu.search"), "menu:search"),
    )
    kb.row(
        _b(t(lang, "menu.wallet"), "menu:wallet"),
        _b(t(lang, "menu.purchases"), "order:page:1"),
    )
    kb.row(
        _b(t(lang, "menu.profile"), "menu:profile"),
        _b(t(lang, "menu.settings"), "menu:settings"),
    )
    if is_seller:
        kb.row(_b(t(lang, "menu.seller_panel"), "seller:panel"))
    else:
        kb.row(_b(t(lang, "menu.become_seller"), "seller:register"))
    kb.row(
        _b(t(lang, "menu.rules"), "menu:rules"),
        _b(t(lang, "menu.support"), "menu:support"),
    )
    if is_admin:
        kb.row(_b(t(lang, "menu.admin"), "admin:home"))
    return kb.as_markup()


def back_keyboard(lang: str, target: str = "menu:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_b(t(lang, "common.back"), target)]]
    )


def back_and(lang: str, text: str, data: str, target: str = "menu:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_b(text, data)],
            [_b(t(lang, "common.back"), target)],
        ]
    )


def click_payment_keyboard(lang: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "wallet.click_pay"), url=url)],
            [_b(t(lang, "common.back"), "menu:wallet")],
        ]
    )


def hamyon_payment_keyboard(lang: str, topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _b(t(lang, "wallet.hamyon_check"), f"hamyon:check:{topup_id}"),
                _b(t(lang, "wallet.hamyon_cancel"), f"hamyon:cancel:{topup_id}"),
            ],
            [_b(t(lang, "common.back"), "menu:wallet")],
        ]
    )


def _pagination(
    kb: InlineKeyboardBuilder, lang: str, prefix: str, page: int, total_pages: int
) -> None:
    if total_pages <= 1:
        return
    row: list[InlineKeyboardButton] = []
    if page > 1:
        row.append(_b(t(lang, "common.prev"), f"{prefix}:{page - 1}"))
    row.append(_b(t(lang, "common.page", page=page, total=total_pages), "noop"))
    if page < total_pages:
        row.append(_b(t(lang, "common.next"), f"{prefix}:{page + 1}"))
    kb.row(*row)


def shop_keyboard(
    lang: str, accounts: list[dict], page: int, total_pages: int, test_mode: bool
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for account in accounts:
        kb.row(
            _b(
                f"#{account['id']} · {account['title'][:28]} · {fmt_sum(account['price'])}",
                f"shop:view:{account['id']}",
            )
        )
    _pagination(kb, lang, "menu:browse", page, total_pages)
    kb.row(_b(t(lang, "menu.search"), "menu:search"))
    kb.row(_b(t(lang, "common.back"), "menu:home"))
    if test_mode:
        kb.row(_b(t(lang, "shop.test_mode_note"), "menu:settings"))
    return kb.as_markup()


def shop_detail_keyboard(
    lang: str, account: dict, can_buy: bool, is_owner: bool
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if can_buy:
        kb.row(_b(t(lang, "shop.buy"), f"shop:buy:{account['id']}"))
    if is_owner:
        kb.row(_b(t(lang, "menu.seller_panel"), "seller:panel"))
    kb.row(_b(t(lang, "common.back"), "menu:browse:1"))
    return kb.as_markup()


def buy_confirm_keyboard(lang: str, account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_b(t(lang, "shop.confirm_yes"), f"shop:confirm:{account_id}")],
            [_b(t(lang, "common.back"), f"shop:view:{account_id}")],
        ]
    )


def order_list_keyboard(
    lang: str, orders: list[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for order in orders:
        kb.row(
            _b(
                f"#{order['id']} · {order['title'][:24]} · "
                f"{t(lang, 'order.status.' + order['status'])}",
                f"order:view:{order['id']}",
            )
        )
    _pagination(kb, lang, "order:page", page, total_pages)
    kb.row(_b(t(lang, "common.back"), "menu:home"))
    return kb.as_markup()


def order_detail_keyboard(lang: str, order: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if order["status"] == "checking":
        kb.row(_b(t(lang, "order.confirm"), f"order:confirm:{order['id']}"))
        kb.row(_b(t(lang, "order.dispute"), f"order:dispute:{order['id']}"))
        kb.row(_b(t(lang, "order.creds"), f"order:creds:{order['id']}"))
    elif order["status"] in ("completed", "auto_completed", "disputed"):
        kb.row(_b(t(lang, "order.creds"), f"order:creds:{order['id']}"))
    kb.row(_b(t(lang, "common.back"), "order:page:1"))
    return kb.as_markup()


def seller_panel_keyboard(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b(t(lang, "seller.add_title"), "seller:add"))
    kb.row(
        _b(t(lang, "seller.accounts_title"), "seller:page:1"),
        _b(t(lang, "seller.sales_title"), "seller:sales"),
    )
    kb.row(_b(t(lang, "seller.withdraw_title"), "seller:withdraw"))
    kb.row(_b(t(lang, "common.back"), "menu:home"))
    return kb.as_markup()


def seller_accounts_keyboard(
    lang: str, accounts: list[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for account in accounts:
        kb.row(
            _b(
                f"#{account['id']} · {account['title'][:24]} · {fmt_sum(account['price'])}",
                f"seller:acc:{account['id']}",
            )
        )
    _pagination(kb, lang, "seller:page", page, total_pages)
    kb.row(_b(t(lang, "seller.add_title"), "seller:add"))
    kb.row(_b(t(lang, "common.back"), "seller:panel"))
    return kb.as_markup()


def seller_account_keyboard(lang: str, account: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if account["status"] == "active":
        kb.row(_b(t(lang, "seller.pause"), f"seller:pause:{account['id']}"))
    elif account["status"] == "paused":
        kb.row(_b(t(lang, "seller.resume"), f"seller:resume:{account['id']}"))
    if account["status"] in ("active", "paused"):
        kb.row(
            _b(t(lang, "seller.edit_price"), f"seller:price:{account['id']}"),
            _b(t(lang, "seller.delete"), f"seller:del:{account['id']}"),
        )
    kb.row(_b(t(lang, "common.back"), "seller:page:1"))
    return kb.as_markup()


def delete_confirm_keyboard(lang: str, account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_b(t(lang, "seller.delete_yes"), f"seller:delok:{account_id}")],
            [_b(t(lang, "common.back"), f"seller:acc:{account_id}")],
        ]
    )


def add_confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_b(t(lang, "seller.add_confirm_yes"), "seller:add:yes")],
            [_b(t(lang, "common.cancel"), "seller:add:cancel")],
        ]
    )


def wallet_keyboard(lang: str, test_mode: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not test_mode:
        kb.row(_b(t(lang, "wallet.topup"), "wallet:topup"))
    kb.row(
        _b(t(lang, "wallet.history"), "wallet:history"),
        _b(t(lang, "wallet.requests"), "wallet:requests"),
    )
    kb.row(_b(t(lang, "common.back"), "menu:home"))
    return kb.as_markup()


def settings_keyboard(
    lang: str, test_mode: bool, is_admin: bool = False
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_b(t(lang, "lang.change"), "set:lang"))
    if is_admin:
        kb.row(
            _b(
                f"{t(lang, 'admin.test_btn')}: "
                f"{t(lang, 'admin.on') if test_mode else t(lang, 'admin.off')}",
                "set:test",
            )
        )
    kb.row(_b(t(lang, "menu.support"), "menu:support"))
    kb.row(_b(t(lang, "common.back"), "menu:home"))
    return kb.as_markup()


def admin_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        _b(t(lang, "admin.stats_btn"), "admin:stats"),
        _b(t(lang, "admin.payments_btn"), "admin:payments:1"),
    )
    kb.row(
        _b(t(lang, "admin.withdrawals_btn"), "admin:withdrawals:1"),
        _b(t(lang, "admin.disputes_btn"), "admin:disputes:1"),
    )
    kb.row(
        _b(t(lang, "admin.sales_btn"), "admin:sales"),
        _b(t(lang, "admin.accounts_btn"), "admin:accounts"),
    )
    kb.row(
        _b(t(lang, "admin.users_btn"), "admin:users"),
        _b(t(lang, "admin.audit_btn"), "admin:audit"),
    )
    kb.row(
        _b(t(lang, "admin.test_btn"), "admin:test"),
        _b(t(lang, "admin.broadcast_btn"), "admin:broadcast"),
    )
    kb.row(_b(t(lang, "common.back"), "menu:home"))
    return kb.as_markup()


def admin_list_keyboard(
    lang: str, prefix: str, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _pagination(kb, lang, prefix, page, total_pages)
    kb.row(_b(t(lang, "admin.back"), "admin:home"))
    return kb.as_markup()


def admin_topup_keyboard(lang: str, topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _b(t(lang, "admin.approve"), f"admin:topup:ok:{topup_id}"),
                _b(t(lang, "admin.reject"), f"admin:topup:no:{topup_id}"),
            ],
            [_b(t(lang, "admin.back"), "admin:payments:1")],
        ]
    )


def admin_withdrawal_keyboard(lang: str, withdrawal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _b(t(lang, "admin.paid"), f"admin:wd:ok:{withdrawal_id}"),
                _b(t(lang, "admin.reject"), f"admin:wd:no:{withdrawal_id}"),
            ],
            [_b(t(lang, "admin.back"), "admin:withdrawals:1")],
        ]
    )


def admin_dispute_keyboard(lang: str, order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _b(t(lang, "admin.refund"), f"admin:disp:refund:{order_id}"),
                _b(t(lang, "admin.release"), f"admin:disp:release:{order_id}"),
            ],
            [_b(t(lang, "admin.check_creds"), f"admin:disp:creds:{order_id}")],
            [_b(t(lang, "admin.back"), "admin:disputes:1")],
        ]
    )


def admin_user_keyboard(lang: str, user: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if user["is_blocked"]:
        kb.row(_b(t(lang, "admin.unblock"), f"admin:user:unblock:{user['id']}"))
    else:
        kb.row(_b(t(lang, "admin.block"), f"admin:user:block:{user['id']}"))
    if user["is_verified"]:
        kb.row(_b(t(lang, "admin.unverify"), f"admin:user:unverify:{user['id']}"))
    else:
        kb.row(_b(t(lang, "admin.verify"), f"admin:user:verify:{user['id']}"))
    kb.row(_b(t(lang, "admin.balance_btn"), f"admin:user:balance:{user['id']}"))
    kb.row(_b(t(lang, "admin.back"), "admin:home"))
    return kb.as_markup()


def test_keyboard(lang: str, enabled: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    label = t(lang, "admin.off") if enabled else t(lang, "admin.on")
    kb.row(_b(f"{t(lang, 'admin.test_toggle')}: {label}", "admin:test:toggle"))
    kb.row(_b(t(lang, "admin.test_seed"), "admin:test:seed"))
    kb.row(_b(t(lang, "admin.back"), "admin:home"))
    return kb.as_markup()


def broadcast_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_b(t(lang, "admin.broadcast_send"), "admin:broadcast:send")],
            [_b(t(lang, "common.cancel"), "admin:home")],
        ]
    )
