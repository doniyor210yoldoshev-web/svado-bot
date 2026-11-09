"""Database layer.

Supports PostgreSQL (production) and SQLite (local development / tests) behind
a single small async API. All SQL is written with ``?`` placeholders and is
translated to PostgreSQL ``$n`` placeholders automatically, so every query is
parameterized and safe from SQL injection.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from config import DATABASE_URL, DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id {{ID}},
        tg_id BIGINT NOT NULL UNIQUE,
        full_name TEXT NOT NULL DEFAULT '',
        username TEXT,
        phone TEXT,
        lang TEXT,
        seller_code TEXT UNIQUE,
        is_seller INTEGER NOT NULL DEFAULT 0,
        is_admin INTEGER NOT NULL DEFAULT 0,
        is_blocked INTEGER NOT NULL DEFAULT 0,
        is_verified INTEGER NOT NULL DEFAULT 0,
        test_mode INTEGER NOT NULL DEFAULT 0,
        balance BIGINT NOT NULL DEFAULT 0,
        test_balance BIGINT NOT NULL DEFAULT 0,
        rating REAL NOT NULL DEFAULT 0,
        rating_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id {{ID}},
        seller_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        login_enc TEXT NOT NULL,
        login_hash TEXT NOT NULL,
        password_enc TEXT NOT NULL,
        price BIGINT NOT NULL CHECK(price > 0),
        status TEXT NOT NULL DEFAULT 'active',
        mode TEXT NOT NULL DEFAULT 'real',
        is_approved INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        sold_at TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_login_hash ON accounts(login_hash)",
    "CREATE INDEX IF NOT EXISTS idx_accounts_seller_status ON accounts(seller_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_accounts_mode_status ON accounts(mode, status)",
    """
    CREATE TABLE IF NOT EXISTS orders (
        id {{ID}},
        account_id BIGINT NOT NULL REFERENCES accounts(id),
        seller_id BIGINT NOT NULL REFERENCES users(id),
        buyer_id BIGINT NOT NULL REFERENCES users(id),
        price BIGINT NOT NULL,
        penalty BIGINT NOT NULL DEFAULT 0,
        penalty_ticks INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'checking',
        mode TEXT NOT NULL DEFAULT 'real',
        check_deadline TEXT NOT NULL,
        auto_complete_at TEXT NOT NULL,
        last_penalty_at TEXT,
        reminder_5_sent INTEGER NOT NULL DEFAULT 0,
        reminder_1_sent INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_seller ON orders(seller_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id {{ID}},
        user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
        order_id BIGINT REFERENCES orders(id) ON DELETE SET NULL,
        amount BIGINT NOT NULL,
        type TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'real',
        reference TEXT UNIQUE,
        description TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)",
    """
    CREATE TABLE IF NOT EXISTS topups (
        id {{ID}},
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount BIGINT NOT NULL,
        receipt_type TEXT NOT NULL,
        receipt_value TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        mode TEXT NOT NULL DEFAULT 'real',
        created_at TEXT NOT NULL,
        reviewed_by BIGINT,
        reviewed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_topups_status ON topups(status)",
    """
    CREATE TABLE IF NOT EXISTS withdrawals (
        id {{ID}},
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount BIGINT NOT NULL,
        card_number TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        mode TEXT NOT NULL DEFAULT 'real',
        created_at TEXT NOT NULL,
        reviewed_by BIGINT,
        reviewed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status)",
    """
    CREATE TABLE IF NOT EXISTS disputes (
        id {{ID}},
        order_id BIGINT NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
        buyer_id BIGINT NOT NULL REFERENCES users(id),
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        resolved_by BIGINT,
        resolved_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_disputes_status ON disputes(status)",
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id {{ID}},
        actor_tg BIGINT,
        action TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)",
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
]

# Columns added after the first release. Kept for safe in-place upgrades.
_MIGRATIONS: dict[str, dict[str, str]] = {
    "users": {
        "lang": "TEXT",
        "is_verified": "INTEGER NOT NULL DEFAULT 0",
        "test_mode": "INTEGER NOT NULL DEFAULT 0",
        "test_balance": "BIGINT NOT NULL DEFAULT 0",
        "rating": "REAL NOT NULL DEFAULT 0",
        "rating_count": "INTEGER NOT NULL DEFAULT 0",
    },
    "accounts": {
        "title": "TEXT NOT NULL DEFAULT ''",
        "description": "TEXT NOT NULL DEFAULT ''",
        "mode": "TEXT NOT NULL DEFAULT 'real'",
        "is_approved": "INTEGER NOT NULL DEFAULT 1",
    },
    "orders": {
        "mode": "TEXT NOT NULL DEFAULT 'real'",
        "reminder_5_sent": "INTEGER NOT NULL DEFAULT 0",
        "reminder_1_sent": "INTEGER NOT NULL DEFAULT 0",
    },
    "transactions": {
        "mode": "TEXT NOT NULL DEFAULT 'real'",
        "reference": "TEXT",
    },
    "topups": {"mode": "TEXT NOT NULL DEFAULT 'real'"},
    "withdrawals": {"mode": "TEXT NOT NULL DEFAULT 'real'"},
}


def _render_schema(dialect: str) -> list[str]:
    id_type = (
        "SERIAL PRIMARY KEY" if dialect == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    return [statement.replace("{{ID}}", id_type).strip() for statement in _SCHEMA]


def _pg_sql(sql: str) -> str:
    """Translate ``?`` placeholders into PostgreSQL ``$1..$n`` placeholders."""
    out: list[str] = []
    index = 0
    for char in sql:
        if char == "?":
            index += 1
            out.append(f"${index}")
        else:
            out.append(char)
    return "".join(out)


class _Executor:
    """Runs queries against a SQLite connection or a PostgreSQL connection."""

    def __init__(self, conn: Any, dialect: str):
        self.conn = conn
        self.dialect = dialect

    def _sql(self, sql: str) -> str:
        return _pg_sql(sql) if self.dialect == "postgres" else sql

    async def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        if self.dialect == "postgres":
            row = await self.conn.fetchrow(self._sql(sql), *params)
            return dict(row) if row is not None else None
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        if self.dialect == "postgres":
            rows = await self.conn.fetch(self._sql(sql), *params)
            return [dict(row) for row in rows]
        cursor = await self.conn.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def execute(self, sql: str, params: tuple = ()) -> int:
        if self.dialect == "postgres":
            status = await self.conn.execute(self._sql(sql), *params)
            try:
                return int(status.rsplit(" ", 1)[-1])
            except (ValueError, IndexError):
                return 0
        cursor = await self.conn.execute(sql, params)
        count = cursor.rowcount
        await cursor.close()
        return int(count or 0)

    async def insert(self, sql: str, params: tuple = ()) -> int:
        if self.dialect == "postgres":
            value = await self.conn.fetchval(self._sql(sql).rstrip().rstrip(";") + " RETURNING id", *params)
            return int(value or 0)
        cursor = await self.conn.execute(sql, params)
        last_id = cursor.lastrowid
        await cursor.close()
        return int(last_id or 0)


class Database:
    def __init__(self, dsn: str = DATABASE_URL, sqlite_path: Path = DB_PATH):
        self.dsn = dsn
        self.sqlite_path = sqlite_path
        self._sqlite: aiosqlite.Connection | None = None
        self._pool: Any = None
        self._lock = asyncio.Lock()
        self.dialect = "postgres" if dsn.startswith(("postgres://", "postgresql://")) else "sqlite"

    @property
    def is_postgres(self) -> bool:
        return self.dialect == "postgres"

    def describe(self) -> str:
        if self.is_postgres:
            return "PostgreSQL"
        return f"SQLite ({self.sqlite_path})"

    async def connect(self) -> None:
        if self.is_postgres:
            try:
                import asyncpg
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "PostgreSQL uchun 'asyncpg' o'rnatilmagan. `pip install asyncpg`"
                ) from exc
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        else:
            self._sqlite = await aiosqlite.connect(str(self.sqlite_path))
            self._sqlite.row_factory = aiosqlite.Row
            await self._sqlite.execute("PRAGMA journal_mode=WAL")
            await self._sqlite.execute("PRAGMA foreign_keys=ON")

        await self._init_schema()
        logger.info("Ma'lumotlar bazasi ulandi (%s)", self.dialect)

    async def _init_schema(self) -> None:
        rendered = _render_schema(self.dialect)
        # Tables first, then column migrations, then indexes (indexes may
        # reference columns added by a migration).
        for statement in rendered:
            if not statement.upper().startswith("CREATE INDEX"):
                await self._run_ddl(statement)
        await self._apply_migrations()
        for statement in rendered:
            if statement.upper().startswith("CREATE INDEX"):
                await self._run_ddl(statement)
        # Older databases may not have the unique reference index.
        await self._run_ddl(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_reference "
            "ON transactions(reference)"
        )

    async def _run_ddl(self, statement: str) -> None:
        if self.is_postgres:
            async with self._pool.acquire() as conn:
                await conn.execute(statement)
        else:
            await self._sqlite.execute(statement)
            await self._sqlite.commit()

    async def _apply_migrations(self) -> None:
        for table, columns in _MIGRATIONS.items():
            existing = await self._table_columns(table)
            for name, definition in columns.items():
                if name in existing:
                    continue
                await self._run_ddl(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                logger.info("Migratsiya: %s.%s qo'shildi", table, name)

    async def _table_columns(self, table: str) -> set[str]:
        if self.is_postgres:
            rows = await self.fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ?",
                (table,),
            )
            return {row["column_name"] for row in rows}
        cursor = await self._sqlite.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        await cursor.close()
        return {row[1] for row in rows}

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._sqlite is not None:
            await self._sqlite.close()
            self._sqlite = None

    async def _executor(self) -> _Executor:
        if self.is_postgres:
            raise RuntimeError("PostgreSQL uchun _conn_context dan foydalaning")
        if self._sqlite is None:
            raise RuntimeError("Ma'lumotlar bazasi ulanmagan")
        return _Executor(self._sqlite, "sqlite")

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[_Executor]:
        if self.is_postgres:
            async with self._pool.acquire() as conn:
                yield _Executor(conn, "postgres")
        else:
            yield await self._executor()

    async def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        if self.is_postgres:
            async with self._conn() as ex:
                return await ex.fetchone(sql, params)
        async with self._lock:
            ex = await self._executor()
            return await ex.fetchone(sql, params)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        if self.is_postgres:
            async with self._conn() as ex:
                return await ex.fetchall(sql, params)
        async with self._lock:
            ex = await self._executor()
            return await ex.fetchall(sql, params)

    async def execute(self, sql: str, params: tuple = ()) -> int:
        if self.is_postgres:
            async with self._conn() as ex:
                return await ex.execute(sql, params)
        async with self._lock:
            ex = await self._executor()
            result = await ex.execute(sql, params)
            await self._sqlite.commit()
            return result

    async def insert(self, sql: str, params: tuple = ()) -> int:
        if self.is_postgres:
            async with self._conn() as ex:
                return await ex.insert(sql, params)
        async with self._lock:
            ex = await self._executor()
            result = await ex.insert(sql, params)
            await self._sqlite.commit()
            return result

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Executor]:
        if self.is_postgres:
            async with self._pool.acquire() as conn:
                tx = conn.transaction()
                await tx.start()
                try:
                    yield _Executor(conn, "postgres")
                    await tx.commit()
                except BaseException:
                    await tx.rollback()
                    raise
        else:
            async with self._lock:
                ex = await self._executor()
                try:
                    yield ex
                    await self._sqlite.commit()
                except BaseException:
                    await self._sqlite.rollback()
                    raise


db = Database()
