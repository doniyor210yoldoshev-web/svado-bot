"""Test bootstrap: force an isolated SQLite database and safe settings."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="marketplace-tests-"))

os.environ["DATABASE_URL"] = ""
os.environ["DB_PATH"] = str(_TMP / "market.db")
os.environ["FSM_DB_PATH"] = str(_TMP / "fsm.db")
os.environ["FERNET_KEY"] = ""
os.environ["FERNET_KEY_PATH"] = str(_TMP / "fernet.key")
os.environ["BOT_TOKEN"] = ""
os.environ["ADMIN_IDS"] = "111"
os.environ["DEFAULT_LANG"] = "uz"
os.environ["PAYMENT_PROVIDER"] = "manual"
os.environ["SEED_TEST_ACCOUNTS"] = "0"

import pytest_asyncio

from database.db import db


@pytest_asyncio.fixture
async def database():
    if db._sqlite is not None:
        await db.close()
    for suffix in ("", "-wal", "-shm"):
        Path(str(db.sqlite_path) + suffix).unlink(missing_ok=True)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()
