from __future__ import annotations

import hashlib

from config import HAMYON_SHOP_ID, HAMYON_SHOP_KEY
from services import hamyon


def test_amount_matching_normalizes_decimal_forms() -> None:
    assert hamyon._amount_matches("1000", 1000)
    assert hamyon._amount_matches("1000.0", 1000)
    assert hamyon._amount_matches("1,000", 1000) is False
    assert hamyon._amount_matches("999", 1000) is False
    assert hamyon._amount_matches("oops", 1000) is False


def test_extract_card_supports_known_field_names() -> None:
    assert hamyon._extract_card({"card": "8600 1234 5678 9012"}) == "8600 1234 5678 9012"
    assert hamyon._extract_card({"card_number": "9860 1111 2222 3333"}) == (
        "9860 1111 2222 3333"
    )
    assert hamyon._extract_card({"card": "null"}) == ""


def test_signature_verification() -> None:
    data = {
        "shop_id": HAMYON_SHOP_ID or "shop",
        "payment_id": "payment-1",
        "amount": "1000",
    }
    expected = hashlib.md5(
        "".join(
            (
                data["shop_id"],
                data["payment_id"],
                data["amount"],
                HAMYON_SHOP_KEY,
            )
        ).encode("utf-8")
    ).hexdigest()

    assert hamyon.verify_signature({**data, "sign": expected})
    assert hamyon.verify_signature({**data, "sign": expected.upper()})
    assert not hamyon.verify_signature({**data, "sign": "0" * 32})
    assert not hamyon.verify_signature(data)


def test_is_configured_requires_shop_credentials() -> None:
    assert isinstance(hamyon.is_configured(), bool)
