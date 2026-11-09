"""Small formatting and parsing helpers shared by handlers and services."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TASHKENT_TZ = ZoneInfo("Asia/Tashkent")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def fmt_sum(amount: float) -> str:
    return f"{int(amount):,}".replace(",", " ") + " so'm"


def fmt_dt(value: str | datetime | None) -> str:
    if value is None:
        return "-"
    dt = parse_dt(value) if isinstance(value, str) else value
    if dt is None:
        return "-"
    local = dt.replace(tzinfo=UTC).astimezone(TASHKENT_TZ)
    return local.strftime("%d.%m.%Y %H:%M")


def fmt_time(value: str | datetime) -> str:
    dt = parse_dt(value) if isinstance(value, str) else value
    if dt is None:
        return "-"
    local = dt.replace(tzinfo=UTC).astimezone(TASHKENT_TZ)
    return local.strftime("%H:%M:%S")


def remaining_seconds(deadline: str | datetime) -> int:
    dt = parse_dt(deadline) if isinstance(deadline, str) else deadline
    if dt is None:
        return 0
    return int((dt - utcnow()).total_seconds())


def esc(value: object) -> str:
    return html.escape(str(value) if value is not None else "")


def code(value: object) -> str:
    return f"<code>{esc(value)}</code>"


def parse_amount(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    if not digits or len(digits) > 12:
        return None
    return int(digits)


def normalize_seller_code(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def clean_card(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


def mask_card(card: str) -> str:
    if len(card) < 8:
        return card
    return f"{card[:4]} **** **** {card[-4:]}"


def deadline_after(minutes: int) -> str:
    return (utcnow() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
