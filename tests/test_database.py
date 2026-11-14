from __future__ import annotations

import pytest

from database.db import db
from services import users


async def test_schema_is_created_and_connect_is_idempotent(database) -> None:
    tables = {
        row["name"]
        for row in await database.fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for expected in (
        "users",
        "wallets",
        "accounts",
        "orders",
        "payments",
        "disputes",
        "refunds",
        "wallet_audit_logs",
    ):
        assert expected in tables

    columns_before = {
        row["name"] for row in await database.fetchall("PRAGMA table_info(users)")
    }
    await database.close()
    await database.connect()
    columns_after = {
        row["name"] for row in await database.fetchall("PRAGMA table_info(users)")
    }
    assert columns_before == columns_after
    assert "phone_verified" in columns_after
    assert "terms_accepted" in columns_after


async def test_transaction_rolls_back_on_error(database) -> None:
    user = await users.ensure_user(9001, "Rollback User", "rollback")
    with pytest.raises(RuntimeError):
        async with db.transaction() as ex:
            await ex.execute(
                "UPDATE users SET balance = balance + 500 WHERE id = ?", (user["id"],)
            )
            raise RuntimeError("boom")
    fresh = await users.get_user(user["id"])
    assert fresh["balance"] == 0

    async with db.transaction() as ex:
        await ex.execute(
            "UPDATE users SET balance = balance + 500 WHERE id = ?", (user["id"],)
        )
    fresh = await users.get_user(user["id"])
    assert fresh["balance"] == 500
