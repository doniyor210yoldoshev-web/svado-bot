from __future__ import annotations

import pytest

from services import security


def test_encrypt_decrypt_roundtrip() -> None:
    token = security.encrypt("secret-login")
    assert token != "secret-login"
    assert security.decrypt(token) == "secret-login"


def test_decrypt_rejects_invalid_token() -> None:
    with pytest.raises(ValueError):
        security.decrypt("not-a-valid-token")


def test_password_hash_and_verify() -> None:
    stored = security.hash_password("s3cret-pass")
    assert stored.startswith("pbkdf2_sha256$")
    assert "s3cret-pass" not in stored
    assert security.verify_password("s3cret-pass", stored)
    assert not security.verify_password("wrong-pass", stored)


def test_verify_password_rejects_malformed_hash() -> None:
    assert not security.verify_password("x", "plain-text")
    assert not security.verify_password("x", "md5$1$salt$hash")


def test_login_hash_is_normalized_and_stable() -> None:
    assert security.login_hash("  User@Example.COM ") == security.login_hash(
        "user@example.com"
    )


def test_generate_seller_code_is_six_digits() -> None:
    code = security.generate_seller_code()
    assert len(code) == 6
    assert code.isdigit()
    assert 100000 <= int(code) <= 999999


def test_validate_amount_bounds() -> None:
    assert security.validate_amount("1000", 1000) == 1000
    assert security.validate_amount("999", 1000) is None
    assert security.validate_amount("5000", 1000, 4000) is None


def test_valid_card_accepts_only_expected_length() -> None:
    assert security.valid_card("8600 1234 5678 9012") == "8600123456789012"
    assert security.valid_card("123") is None
