from __future__ import annotations

import json

import pytest

from handlers.helpers import resolve_menu_action
from keyboards.inline import (
    admin_dispute_keyboard,
    order_payment_keyboard,
    seller_dispute_keyboard,
    terms_keyboard,
)
from keyboards.reply import main_menu_reply
from services.i18n import locale_path, t

LANGS = ("uz", "ru", "en")

CRITICAL_KEYS = (
    "menu.sell",
    "menu.my_accounts",
    "menu.my_orders",
    "menu.history",
    "menu.help",
    "reg.phone_prompt",
    "reg.terms_title",
    "reg.accept",
    "shop.payment_title",
    "shop.payment_check",
    "shop.payment_cancel",
    "shop.order_cancelled_timeout",
    "shop.seller_order_cancelled_timeout",
    "dispute.seller_yes",
    "dispute.seller_no",
    "dispute.seller_answered",
    "notify.dispute_seller_timeout",
    "notify.dispute_hold_buyer",
    "notify.dispute_hold_seller",
)


def _load(lang: str) -> dict[str, str]:
    with locale_path(lang).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_locales_have_identical_keys() -> None:
    reference = set(_load("uz"))
    for lang in LANGS[1:]:
        assert set(_load(lang)) == reference


@pytest.mark.parametrize("lang", LANGS)
def test_critical_keys_exist(lang: str) -> None:
    data = _load(lang)
    for key in CRITICAL_KEYS:
        assert key in data
        assert data[key].strip()


@pytest.mark.parametrize("lang", LANGS)
def test_every_reply_menu_button_is_wired(lang: str) -> None:
    keyboard = main_menu_reply(lang, is_seller=True, is_admin=False)
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert len(labels) == 9
    actions = [resolve_menu_action(label) for label in labels]
    assert all(action is not None for action in actions)
    assert len(set(actions)) == 9


def test_translator_falls_back_to_key() -> None:
    assert t("uz", "definitely.missing.key") == "definitely.missing.key"
    assert t("unknown", "menu.browse") == t("uz", "menu.browse")


def test_admin_dispute_panel_is_read_only() -> None:
    keyboard = admin_dispute_keyboard("uz", 5)
    payloads = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert all("refund" not in (payload or "") for payload in payloads)
    assert all("release" not in (payload or "") for payload in payloads)


def test_purchase_and_dispute_keyboards_have_expected_actions() -> None:
    payment = order_payment_keyboard("uz", 7)
    payment_payloads = [
        button.callback_data for row in payment.inline_keyboard for button in row
    ]
    assert "order:paycheck:7" in payment_payloads
    assert "order:paycancel:7" in payment_payloads

    dispute = seller_dispute_keyboard("uz", 8)
    dispute_payloads = [
        button.callback_data for row in dispute.inline_keyboard for button in row
    ]
    assert "disp:seller:ha:8" in dispute_payloads
    assert "disp:seller:no:8" in dispute_payloads

    terms = terms_keyboard("uz")
    terms_payloads = [
        button.callback_data for row in terms.inline_keyboard for button in row
    ]
    assert terms_payloads == ["terms:accept"]
