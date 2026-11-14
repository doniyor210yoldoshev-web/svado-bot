from __future__ import annotations

from utils import (
    clean_card,
    deadline_after,
    esc,
    fmt_dt,
    fmt_sum,
    mask_card,
    parse_amount,
    parse_dt,
    remaining_seconds,
)


def test_fmt_sum_uses_space_separator() -> None:
    assert fmt_sum(1234567) == "1 234 567 so'm"
    assert fmt_sum(0) == "0 so'm"


def test_parse_amount_keeps_only_digits() -> None:
    assert parse_amount("1 000 000 so'm") == 1000000
    assert parse_amount("") is None
    assert parse_amount("1234567890123") is None


def test_esc_escapes_html() -> None:
    assert esc("<b>hello & bye</b>") == "&lt;b&gt;hello &amp; bye&lt;/b&gt;"


def test_parse_dt_handles_invalid_values() -> None:
    assert parse_dt("2026-01-02T03:04:05") is not None
    assert parse_dt("not-a-date") is None
    assert parse_dt(None) is None


def test_fmt_dt_none_is_dash() -> None:
    assert fmt_dt(None) == "-"


def test_remaining_seconds_is_positive_for_future_deadline() -> None:
    assert remaining_seconds(deadline_after(5)) > 0
    assert remaining_seconds(deadline_after(-5)) < 0


def test_card_helpers() -> None:
    assert clean_card("8600-1234 5678 9012") == "8600123456789012"
    assert mask_card("8600123456789012") == "8600 **** **** 9012"
    assert mask_card("123") == "123"
