"""Lightweight HTTP health server.

Render (and most PaaS providers) require a web service to bind to ``$PORT``.
The Telegram bot itself only does long polling, so this tiny server exists
solely to expose a health endpoint. It has no effect when ``PORT`` is unset.

Endpoints:
    GET /health  -> JSON status (200 when the database is reachable)
    GET /        -> "OK"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from database.db import db

logger = logging.getLogger(__name__)


def _response(status: str, content_type: str, body: bytes) -> bytes:
    headers = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return headers.encode("latin-1") + body


async def start_health_server() -> asyncio.AbstractServer | None:
    port_raw = os.getenv("PORT")
    if not port_raw:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        logger.error("PORT noto'g'ri: %s", port_raw)
        return None

    started_at = time.monotonic()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.read(2048)
            request_line = request.split(b"\r\n", 1)[0].decode("latin-1", "ignore")
            parts = request_line.split()
            path = parts[1].split("?", 1)[0] if len(parts) >= 2 else "/"

            if path in ("/health", "/healthz", "/ping"):
                db_ok = True
                try:
                    await db.fetchone("SELECT 1 AS ok")
                except Exception as exc:  # noqa: BLE001 - any DB error means degraded
                    logger.warning("Health DB tekshiruvi muvaffaqiyatsiz: %s", exc)
                    db_ok = False
                body = json.dumps(
                    {
                        "status": "ok" if db_ok else "degraded",
                        "database": "ok" if db_ok else "down",
                        "uptime_seconds": int(time.monotonic() - started_at),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                ).encode("utf-8")
                response = _response(
                    "200 OK" if db_ok else "503 Service Unavailable",
                    "application/json",
                    body,
                )
            elif path == "/":
                response = _response("200 OK", "text/plain", b"OK")
            else:
                response = _response("404 Not Found", "text/plain", b"Not Found")

            writer.write(response)
            await writer.drain()
        except Exception as exc:  # noqa: BLE001 - never crash the health server
            logger.debug("Health so'rovida xatolik: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Health ulanishini yopishda xatolik: %s", exc)

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    logger.info("Health server %s-portda ishga tushdi (/health)", port)
    return server
