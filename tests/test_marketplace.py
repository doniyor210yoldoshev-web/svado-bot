from __future__ import annotations

import pytest

from database.db import db
from services import marketplace, users
from services.errors import ServiceError
from utils import parse_dt


async def _setup(database, *, mode: str = "test", price: int = 10000):
    seller = await users.ensure_user(3001, "Seller", "seller")
    seller = await users.register_seller(seller["id"], "+998901111111")
    buyer = await users.ensure_user(3002, "Buyer", "buyer")
    account_id = await marketplace.create_account(
        seller_id=seller["id"],
        title="Telegram account",
        description="Verified account",
        login="login@example.com",
        password="pass1234",
        price=price,
        mode=mode,
    )
    return seller, buyer, account_id


async def test_escrow_order_has_five_minute_deadline(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    assert order["status"] == "pending_payment"
    delta = parse_dt(order["payment_deadline"]) - parse_dt(order["created_at"])
    assert 290 <= delta.total_seconds() <= 310


async def test_credentials_hidden_until_payment_confirmed(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )

    with pytest.raises(ServiceError):
        await marketplace.get_credentials(order["id"], buyer["id"])

    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "reserved"

    await marketplace.attach_escrow_payment(
        order["id"], buyer["id"], "pay-1", 10000, "8600 0000 0000 0000"
    )
    info = await marketplace.confirm_escrow_payment(order["id"], "pay-1")
    assert info["order"]["status"] == "checking"

    login, password, _extra = await marketplace.get_credentials(
        order["id"], buyer["id"]
    )
    assert login == "login@example.com"
    assert password == "pass1234"

    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "sold"

    with pytest.raises(ServiceError):
        await marketplace.get_credentials(order["id"], seller["id"])


async def test_settlement_applies_seven_percent_commission(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-2")
    info = await marketplace.confirm_order(order["id"], buyer["id"])

    assert info["seller_share"] == 9300
    assert info["commission"] == 700
    assert info["seller_share"] + info["commission"] == info["total"]

    fresh_seller = await users.get_user(seller["id"])
    assert fresh_seller["test_balance"] == 9300


async def test_buyer_cancel_returns_account_to_market(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    cancelled = await marketplace.cancel_pending_order(order["id"], buyer["id"])
    assert cancelled is not None

    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "cancelled"
    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "active"

    with pytest.raises(ServiceError):
        await marketplace.cancel_pending_order(order["id"], buyer["id"])


async def test_expired_orders_are_cancelled(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await db.execute(
        "UPDATE orders SET payment_deadline = ? WHERE id = ?",
        ("2000-01-01T00:00:00", order["id"]),
    )
    events = await marketplace.expire_pending_orders()
    assert any(event["order"]["id"] == order["id"] for event in events)
    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "cancelled"
    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "active"


async def test_seller_yes_triggers_automatic_refund(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-3")

    balance_before = (await users.get_user(buyer["id"]))["test_balance"]
    await marketplace.create_dispute(order["id"], buyer["id"], "Akkaunt ishlamaydi")

    result = await marketplace.handle_seller_dispute_response(
        order["id"], seller["id"], "HA"
    )
    assert result["action"] == "refund"

    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "refunded"
    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "active"
    fresh_buyer = await users.get_user(buyer["id"])
    assert fresh_buyer["test_balance"] == balance_before + int(order["price"])

    dispute = await db.fetchone(
        "SELECT * FROM disputes WHERE order_id = ?", (order["id"],)
    )
    assert dispute["status"] == "refunded"
    assert dispute["auto_resolved"] == 1
    assert dispute["seller_response"] == "HA"


async def test_seller_no_with_clean_history_locks_funds(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-4")
    await marketplace.create_dispute(order["id"], buyer["id"], "Parol ishlamaydi")

    result = await marketplace.handle_seller_dispute_response(
        order["id"], seller["id"], "YO_Q"
    )
    assert result["action"] == "hold"
    dispute = await db.fetchone(
        "SELECT * FROM disputes WHERE order_id = ?", (order["id"],)
    )
    assert dispute["status"] == "locked_hold"

    with pytest.raises(ServiceError):
        await marketplace.handle_seller_dispute_response(
            order["id"], seller["id"], "HA"
        )


async def test_seller_no_with_evidence_refunds_buyer(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-evidence")
    await marketplace.create_dispute(
        order["id"],
        buyer["id"],
        "Parol ishlamaydi",
        evidence_type="text",
        evidence_content="Telegram says the password is invalid.",
    )

    result = await marketplace.handle_seller_dispute_response(
        order["id"], seller["id"], "YO_Q"
    )
    assert result["action"] == "refund"

    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "refunded"


async def test_late_payment_reactivates_cancelled_order(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.cancel_expired_order(order["id"])

    info = await marketplace.handle_late_order_payment(order["id"], "pay-late")
    assert info is not None
    assert info["action"] == "reactivated"

    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "checking"
    assert fresh_order["payment_id"] == "pay-late"
    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "sold"

    repeat = await marketplace.handle_late_order_payment(order["id"], "pay-late")
    assert repeat is not None
    assert repeat["action"] == "already"


async def test_late_payment_credits_wallet_when_account_is_gone(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    first, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.cancel_expired_order(first["id"])

    second, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(second["id"], "pay-second")
    balance_before = int((await users.get_user(buyer["id"]))["test_balance"])

    info = await marketplace.handle_late_order_payment(first["id"], "pay-first-late")
    assert info is not None
    assert info["action"] == "credited"
    fresh_buyer = await users.get_user(buyer["id"])
    assert fresh_buyer["test_balance"] == balance_before + int(first["price"])

    refund = await db.fetchone(
        "SELECT * FROM refunds WHERE reference = ?", ("latepay:pay-first-late",)
    )
    assert refund is not None
    assert refund["status"] == "completed"


async def test_unresponsive_seller_timeout_refunds_buyer(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-5")
    await marketplace.create_dispute(order["id"], buyer["id"], "Akkaunt bloklangan")
    await db.execute(
        "UPDATE disputes SET created_at = ? WHERE order_id = ?",
        ("2000-01-01T00:00:00", order["id"]),
    )

    events = await marketplace.check_dispute_timeouts()
    assert len(events) == 1
    assert events[0]["action"] == "refund"
    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "refunded"


async def test_seed_test_accounts_is_idempotent(database) -> None:
    created = await marketplace.seed_test_accounts(10, target_mode="test")
    assert created == 10
    assert await marketplace.seed_test_accounts(10, target_mode="test") == 0

    rows = await db.fetchall(
        "SELECT COUNT(*) AS c FROM accounts WHERE mode = 'test' AND status = 'active'"
    )
    assert rows[0]["c"] == 10
