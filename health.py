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
from urllib.parse import parse_qsl

from aiogram import Bot

from database.db import db
from services import click as click_service

logger = logging.getLogger(__name__)

_MAX_REQUEST_BYTES = 131_072


def _response(status: str, content_type: str, body: bytes) -> bytes:
    headers = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return headers.encode("latin-1") + body


async def _read_request(
    reader: asyncio.StreamReader,
) -> tuple[str, dict[str, str], bytes]:
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = await reader.read(1024)
        if not chunk:
            break
        buffer += chunk
        if len(buffer) > _MAX_REQUEST_BYTES:
            break
    head, _, body = buffer.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    request_line = lines[0].decode("latin-1", "ignore") if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        headers[name.decode("latin-1", "ignore").strip().lower()] = value.decode(
            "latin-1", "ignore"
        ).strip()
    try:
        length = int(headers.get("content-length", "0") or "0")
    except ValueError:
        length = 0
    length = min(length, _MAX_REQUEST_BYTES)
    while len(body) < length:
        chunk = await reader.read(length - len(body))
        if not chunk:
            break
        body += chunk
    return request_line, headers, body


def _parse_body(headers: dict[str, str], body: bytes) -> dict[str, str]:
    if not body:
        return {}
    content_type = headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = json.loads(body.decode("utf-8", "ignore"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items()}
    text = body.decode("utf-8", "ignore")
    return {key: value for key, value in parse_qsl(text, keep_blank_values=True)}


async def start_health_server(bot: Bot | None = None) -> asyncio.AbstractServer | None:
    port_raw = os.getenv("PORT")
    if not port_raw:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        logger.error("PORT noto'g'ri: %s", port_raw)
        return None

    if bot is not None:
        click_service.set_bot(bot)

    started_at = time.monotonic()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line, headers, body = await _read_request(reader)
            parts = request_line.split()
            method = parts[0].upper() if parts else "GET"
            path = parts[1].split("?", 1)[0] if len(parts) >= 2 else "/"

            if path in ("/click/prepare", "/click/complete", "/click"):
                if method != "POST":
                    response = _response(
                        "405 Method Not Allowed",
                        "text/plain",
                        b"Method Not Allowed",
                    )
                else:
                    data = _parse_body(headers, body)
                    try:
                        result = await click_service.handle_request(data)
                    except Exception:
                        logger.exception("Click so'rovida xatolik")
                        result = {
                            "click_trans_id": data.get("click_trans_id", ""),
                            "merchant_trans_id": data.get("merchant_trans_id", ""),
                            "error": -8,
                            "error_note": "Error in request from click",
                        }
                    response = _response(
                        "200 OK",
                        "application/json",
                        json.dumps(result).encode("utf-8"),
                    )
            elif path in ("/health", "/healthz", "/ping"):
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
