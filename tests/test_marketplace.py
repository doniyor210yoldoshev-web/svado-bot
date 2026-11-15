from __future__ import annotations

from datetime import timedelta

import pytest

from database.db import db
from services import marketplace, users
from services.errors import ServiceError
from utils import parse_dt, utcnow


async def _setup(database, *, mode: str = "test", price: int = 10000):
    seller = await users.ensure_user(3001, "Seller", "seller")
    seller = await users.register_seller(seller["id"], "+998901111111")
    buyer = await users.ensure_user(3002, "Buyer", "buyer")
    await db.execute(
        "UPDATE users SET test_mode = ? WHERE id = ?",
        (1 if mode == "test" else 0, buyer["id"]),
    )
    account_id = await marketplace.create_account(
        seller_id=seller["id"],
        title="Telegram account",
        description="Verified account",
        login="login@example.com",
        password="pass1234",
        price=price,
        mode=mode,
    )
    await db.execute(
        "UPDATE accounts SET is_approved = 1, verification_status = 'verified' "
        "WHERE id = ?",
        (account_id,),
    )
    return seller, await users.get_user(buyer["id"]), account_id


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


async def test_seller_yes_releases_funds_without_buyer_refund(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-3")

    buyer_before = int((await users.get_user(buyer["id"]))["test_balance"])
    seller_before = int((await users.get_user(seller["id"]))["test_balance"])
    await marketplace.create_dispute(order["id"], buyer["id"], "Akkaunt ishlamaydi")

    result = await marketplace.handle_seller_dispute_response(
        order["id"], seller["id"], "HA"
    )
    assert result["action"] == "release"

    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "completed"
    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "sold"
    fresh_buyer = await users.get_user(buyer["id"])
    assert int(fresh_buyer["test_balance"]) == buyer_before
    fresh_seller = await users.get_user(seller["id"])
    assert int(fresh_seller["test_balance"]) == seller_before + 9300

    dispute = await db.fetchone(
        "SELECT * FROM disputes WHERE order_id = ?", (order["id"],)
    )
    assert dispute["status"] == "released"
    assert dispute["auto_resolved"] == 1
    assert dispute["seller_response"] == "HA"


async def test_repeat_bad_seller_yes_still_refunds_buyer(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-bad-seller")
    await db.execute(
        "UPDATE users SET failed_disputes = 2 WHERE id = ?", (seller["id"],)
    )
    balance_before = int((await users.get_user(buyer["id"]))["test_balance"])
    await marketplace.create_dispute(order["id"], buyer["id"], "Parol ishlamaydi")

    result = await marketplace.handle_seller_dispute_response(
        order["id"], seller["id"], "HA"
    )
    assert result["action"] == "refund"

    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "refunded"
    fresh_account = await marketplace.get_account(account_id)
    assert fresh_account["status"] == "paused"
    fresh_buyer = await users.get_user(buyer["id"])
    assert int(fresh_buyer["test_balance"]) == balance_before + int(order["price"])


async def test_seller_no_with_clean_history_refunds_and_pauses_account(database) -> None:
    seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-4")
    await marketplace.create_dispute(order["id"], buyer["id"], "Parol ishlamaydi")

    result = await marketplace.handle_seller_dispute_response(
        order["id"], seller["id"], "YO_Q"
    )
    assert result["action"] == "refund"
    dispute = await db.fetchone(
        "SELECT * FROM disputes WHERE order_id = ?", (order["id"],)
    )
    assert dispute["status"] == "refunded"
    fresh_order = await marketplace.get_order(order["id"])
    fresh_account = await marketplace.get_account(account_id)
    assert fresh_order["status"] == "refunded"
    assert fresh_account["status"] == "paused"

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


async def test_penalty_is_capped_to_available_balance(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-pen")

    await db.execute(
        "UPDATE users SET test_balance = 500 WHERE id = ?", (buyer["id"],)
    )
    await db.execute(
        "UPDATE orders SET check_deadline = ? WHERE id = ?",
        ("2000-01-01T00:00:00", order["id"]),
    )

    await marketplace.accrue_penalties()

    fresh_buyer = await users.get_user(buyer["id"])
    fresh_order = await marketplace.get_order(order["id"])
    assert int(fresh_buyer["test_balance"]) == 0
    assert int(fresh_order["penalty"]) == 500
    assert int(fresh_order["penalty_ticks"]) > 0

    await marketplace.accrue_penalties()
    fresh_buyer = await users.get_user(buyer["id"])
    fresh_order = await marketplace.get_order(order["id"])
    assert int(fresh_buyer["test_balance"]) == 0
    assert int(fresh_order["penalty"]) == 500


async def test_inspection_window_is_five_minutes_after_payment(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    info = await marketplace.confirm_escrow_payment(order["id"], "pay-window")
    fresh_order = info["order"]
    delta = parse_dt(fresh_order["check_deadline"]) - parse_dt(fresh_order["updated_at"])
    assert 295 <= delta.total_seconds() <= 305


async def test_penalty_is_seven_percent_per_minute_after_deadline(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-rate")
    await db.execute(
        "UPDATE users SET test_balance = 100000 WHERE id = ?", (buyer["id"],)
    )
    deadline = (utcnow() - timedelta(seconds=61)).isoformat(timespec="seconds")
    await db.execute(
        "UPDATE orders SET check_deadline = ?, last_reminder_minute = 0 WHERE id = ?",
        (deadline, order["id"]),
    )

    events = await marketplace.accrue_penalties()

    fresh_order = await marketplace.get_order(order["id"])
    assert int(fresh_order["penalty"]) == 1400
    assert any(event["key"] == "notify.penalty" for event in events)


async def test_reminder_is_sent_each_minute_during_window(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.confirm_escrow_payment(order["id"], "pay-reminder")
    deadline = (utcnow() + timedelta(minutes=4)).isoformat(timespec="seconds")
    await db.execute(
        "UPDATE orders SET check_deadline = ?, last_reminder_minute = 5 WHERE id = ?",
        (deadline, order["id"]),
    )

    events = await marketplace.accrue_penalties()

    reminders = [
        event for event in events if event["key"] == "notify.reminder_minute"
    ]
    assert reminders
    assert reminders[0]["kwargs"]["minutes"] in (3, 4)


async def test_escrow_rejects_mode_mismatch(database) -> None:
    _seller, buyer, account_id = await _setup(database, mode="test")
    await db.execute(
        "UPDATE users SET test_mode = 0 WHERE id = ?", (buyer["id"],)
    )

    with pytest.raises(ServiceError):
        await marketplace.create_escrow_order(buyer["id"], account_id)

    account = await marketplace.get_account(account_id)
    assert account["status"] == "active"


async def test_payment_confirmation_rejects_another_payment_id(database) -> None:
    _seller, buyer, account_id = await _setup(database)
    order, _account, _seller_row = await marketplace.create_escrow_order(
        buyer["id"], account_id
    )
    await marketplace.attach_escrow_payment(
        order["id"], buyer["id"], "pay-right", 10000, "8600 0000 0000 0000"
    )

    with pytest.raises(ServiceError):
        await marketplace.confirm_escrow_payment(order["id"], "pay-wrong")

    fresh_order = await marketplace.get_order(order["id"])
    assert fresh_order["status"] == "pending_payment"


async def test_get_account_resolves_internal_id_before_display_id(database) -> None:
    seller, _buyer_row, first_id = await _setup(database, mode="test")
    second_id = await marketplace.create_account(
        seller_id=seller["id"],
        title="Second account",
        description="Collision check",
        login="second@example.com",
        password="pass1234",
        price=10000,
        mode="test",
    )
    await db.execute(
        "UPDATE accounts SET display_id = ? WHERE id = ?", (first_id, second_id)
    )

    assert (await marketplace.get_account(first_id))["id"] == first_id
    assert (await marketplace.get_account(second_id))["id"] == second_id


async def test_seller_cannot_edit_or_delete_a_reserved_account(database) -> None:
    seller, buyer, account_id = await _setup(database)
    await marketplace.create_escrow_order(buyer["id"], account_id)

    with pytest.raises(ServiceError):
        await marketplace.update_account_price(account_id, seller["id"], 20000)
    with pytest.raises(ServiceError):
        await marketplace.set_account_paused(account_id, seller["id"], True)
    with pytest.raises(ServiceError):
        await marketplace.delete_account(account_id, seller["id"])

    account = await marketplace.get_account(account_id)
    assert account["status"] == "reserved"
    assert int(account["price"]) == 10000
