"""Wallet integration tests: top-ups, idempotency, concurrency, refunds, ACL."""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from config import MIN_TOPUP
from services import click, hamyon, marketplace, payment, users
from services.errors import InsufficientFunds, ServiceError


async def _buyer(tg_id: int = 5001, name: str = "Buyer") -> dict:
    return await users.ensure_user(tg_id, name, f"buyer{tg_id}")


async def _seller_and_account(price: int = 10000, mode: str = "real") -> tuple[dict, int]:
    seller = await users.ensure_user(5002, "Seller", "seller")
    seller = await users.register_seller(seller["id"], "+998905550002")
    account_id = await marketplace.create_account(
        seller_id=seller["id"],
        title="Wallet test account",
        description="For wallet tests",
        login="wallet@example.com",
        password="secret123",
        price=price,
        mode=mode,
    )
    return seller, account_id


async def _credit(user_id: int, amount: int) -> None:
    await payment.admin_adjust_balance(user_id, amount, "test credit", 111)


async def test_topup_is_pending_until_approved(database) -> None:
    user = await _buyer()
    topup = await payment.create_topup(user["id"], 5000, "text", "receipt-1234")

    assert topup["status"] == "pending"
    assert topup["public_id"].startswith("PAY-")
    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 0
    assert await payment.list_transactions(user["id"]) == []
    wallet = await payment.get_wallet(user["id"])
    assert int(wallet["balance"]) == 0
    summary = await payment.wallet_summary(fresh)
    assert summary["pending_topups"] == 5000
    assert summary["total_in"] == 0


async def test_topup_respects_min_and_max_limits(database) -> None:
    user = await _buyer()
    with pytest.raises(ServiceError):
        await payment.create_topup(user["id"], MIN_TOPUP - 1, "text", "receipt")
    with pytest.raises(ServiceError):
        await payment.create_topup(user["id"], 50_000_001, "text", "receipt")


async def test_approve_topup_credits_exactly_once(database) -> None:
    user = await _buyer()
    topup = await payment.create_topup(user["id"], 5000, "text", "receipt-1234")

    info = await payment.approve_topup(topup["id"], 111)
    assert info["amount"] == 5000

    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 5000
    assert int((await payment.get_wallet(user["id"]))["balance"]) == 5000
    rows = await payment.list_transactions(user["id"])
    assert len(rows) == 1
    assert rows[0]["type"] == "topup"
    assert rows[0]["amount"] == 5000
    assert rows[0]["status"] == "completed"

    with pytest.raises(ServiceError):
        await payment.approve_topup(topup["id"], 111)
    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 5000
    assert len(await payment.list_transactions(user["id"])) == 1

    audit = await database.fetchall("SELECT * FROM wallet_audit_logs")
    assert len(audit) == 1
    assert audit[0]["action"] == "topup.approve"
    assert audit[0]["balance_before"] == 0
    assert audit[0]["balance_after"] == 5000


async def test_concurrent_topup_approvals_credit_once(database) -> None:
    user = await _buyer()
    topup = await payment.create_topup(user["id"], 7000, "text", "receipt-concurrent")

    results = await asyncio.gather(
        payment.approve_topup(topup["id"], 111),
        payment.approve_topup(topup["id"], 111),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ServiceError) for result in results) == 1

    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 7000
    assert len(await payment.list_transactions(user["id"])) == 1


async def test_click_complete_is_idempotent(database) -> None:
    user = await _buyer()
    topup = await payment.create_click_topup(user["id"], 6000)
    assert topup["public_id"].startswith("PAY-")

    first = await payment.complete_click_topup(topup["id"], "click-1", "paydoc-1")
    second = await payment.complete_click_topup(topup["id"], "click-1", "paydoc-1")
    assert first is not None and not first.get("already")
    assert second is not None and second.get("already")

    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 6000
    assert len(await payment.list_transactions(user["id"])) == 1


async def test_click_callback_rejects_forged_signature(database, monkeypatch) -> None:
    monkeypatch.setattr(click, "CLICK_SERVICE_ID", "svc-1")
    monkeypatch.setattr(click, "CLICK_MERCHANT_ID", "merchant-1")
    monkeypatch.setattr(click, "CLICK_SECRET_KEY", "secret-key")
    user = await _buyer()
    topup = await payment.create_click_topup(user["id"], 6000)

    forged = {
        "action": "1",
        "service_id": "svc-1",
        "click_trans_id": "c-1",
        "click_paydoc_id": "p-1",
        "merchant_trans_id": str(topup["id"]),
        "merchant_prepare_id": str(topup["id"]),
        "amount": str(topup["amount"]),
        "sign_time": "2026-01-01 00:00:00",
        "error": "0",
        "sign_string": "0" * 32,
    }
    response = await click.handle_request(forged)
    assert response["error"] == -1
    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 0
    assert await payment.list_transactions(user["id"]) == []


async def test_hamyon_callback_requires_valid_signature(database, monkeypatch) -> None:
    monkeypatch.setattr(hamyon, "HAMYON_SHOP_ID", "shop-1")
    monkeypatch.setattr(hamyon, "HAMYON_SHOP_KEY", "secret-key")
    user = await _buyer()
    topup = await payment.create_hamyon_topup(user["id"], 8000)
    await payment.mark_hamyon_created(topup["id"], "hp-1")

    fake = {
        "shop_id": "shop-1",
        "payment_id": "hp-1",
        "order_id": str(topup["id"]),
        "amount": str(topup["amount"]),
        "status": "paid",
        "sign": "0" * 32,
    }
    assert await hamyon.handle_callback(fake) == {"ok": False}
    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 0

    sign = hashlib.md5(
        f"shop-1hp-1{topup['amount']}secret-key".encode()
    ).hexdigest()
    valid = {**fake, "sign": sign}
    assert await hamyon.handle_callback(valid) == {"ok": True}
    assert await hamyon.handle_callback(valid) == {"ok": True}

    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == int(topup["amount"])
    assert len(await payment.list_transactions(user["id"])) == 1


async def test_wallet_purchase_deducts_and_records(database) -> None:
    _seller, account_id = await _seller_and_account(price=10000)
    buyer = await _buyer()
    await _credit(buyer["id"], 15000)

    order, account, _seller_row = await marketplace.create_order(buyer["id"], account_id)
    assert order["status"] == "checking"
    assert account["id"] == account_id

    fresh = await users.get_user(buyer["id"])
    assert int(fresh["balance"]) == 5000
    assert int((await payment.get_wallet(buyer["id"]))["balance"]) == 5000
    rows = await payment.list_transactions(buyer["id"])
    purchase = next(row for row in rows if row["type"] == "purchase")
    assert purchase["amount"] == -10000
    assert purchase["order_id"] == order["id"]


async def test_wallet_purchase_with_insufficient_balance(database) -> None:
    _seller, account_id = await _seller_and_account(price=10000)
    buyer = await _buyer()

    with pytest.raises(InsufficientFunds):
        await marketplace.create_order(buyer["id"], account_id)

    fresh = await users.get_user(buyer["id"])
    assert int(fresh["balance"]) == 0
    account = await marketplace.get_account(account_id)
    assert account["status"] == "active"
    assert await payment.list_transactions(buyer["id"]) == []


async def test_concurrent_purchases_only_one_succeeds(database) -> None:
    _seller, account_id = await _seller_and_account(price=10000)
    first = await _buyer(5003, "First")
    second = await _buyer(5004, "Second")
    await _credit(first["id"], 10000)
    await _credit(second["id"], 10000)

    results = await asyncio.gather(
        marketplace.create_order(first["id"], account_id),
        marketplace.create_order(second["id"], account_id),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Exception) for result in results) == 1

    orders = await database.fetchall("SELECT * FROM orders")
    assert len(orders) == 1
    account = await marketplace.get_account(account_id)
    assert account["status"] == "sold"


async def test_transaction_access_is_scoped_to_owner(database) -> None:
    owner = await _buyer(5005, "Owner")
    stranger = await _buyer(5006, "Stranger")
    await _credit(owner["id"], 3000)
    transaction = (await payment.list_transactions(owner["id"]))[0]

    assert await payment.get_transaction(transaction["id"], owner["id"]) is not None
    assert await payment.get_transaction(transaction["id"], stranger["id"]) is None


async def test_admin_adjustment_is_audited_and_never_negative(database) -> None:
    user = await _buyer()
    updated, balance = await payment.admin_adjust_balance(user["id"], 5000, "bonus", 111)
    assert balance == 5000
    assert int(updated["balance"]) == 5000

    updated, balance = await payment.admin_adjust_balance(user["id"], -1500, "fix", 111)
    assert balance == 3500
    assert int(updated["balance"]) == 3500

    with pytest.raises(ServiceError):
        await payment.admin_adjust_balance(user["id"], -99_999, "too much", 111)
    fresh = await users.get_user(user["id"])
    assert int(fresh["balance"]) == 3500

    logs = await payment.list_wallet_audit_logs()
    assert len(logs) == 2
    assert logs[0]["balance_before"] == 5000
    assert logs[0]["balance_after"] == 3500
    assert logs[0]["actor_tg"] == 111


async def test_admin_refund_credits_wallet_with_refund_type(database) -> None:
    user = await _buyer()
    updated, balance = await payment.admin_refund_wallet(user["id"], 4000, "refund", 111)
    assert balance == 4000
    assert int(updated["balance"]) == 4000

    rows = await payment.list_transactions(user["id"])
    assert len(rows) == 1
    assert rows[0]["type"] == "refund"
    assert rows[0]["amount"] == 4000

    logs = await payment.list_wallet_audit_logs()
    assert logs[0]["action"] == "wallet.refund"


async def test_withdrawal_rejection_refunds_exactly_once(database) -> None:
    user = await _buyer()
    await _credit(user["id"], 10000)
    withdrawal = await payment.create_withdrawal(user["id"], 5000, "8600 1234 5678 9012")
    assert int((await users.get_user(user["id"]))["balance"]) == 5000

    await payment.reject_withdrawal(withdrawal["id"], 111)
    assert int((await users.get_user(user["id"]))["balance"]) == 10000

    with pytest.raises(ServiceError):
        await payment.reject_withdrawal(withdrawal["id"], 111)
    assert int((await users.get_user(user["id"]))["balance"]) == 10000

    rows = await payment.list_transactions(user["id"])
    assert [row["type"] for row in rows].count("refund") == 1
