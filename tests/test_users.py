from __future__ import annotations

from services import users


async def test_ensure_user_creates_and_updates_profile(database) -> None:
    user = await users.ensure_user(2001, "Ali Valiyev", "ali")
    assert user["tg_id"] == 2001
    assert user["full_name"] == "Ali Valiyev"
    assert user["phone_verified"] == 0
    assert user["terms_accepted"] == 0
    assert user["is_admin"] == 0

    updated = await users.ensure_user(2001, "Ali V.", "ali_new")
    assert updated["id"] == user["id"]
    assert updated["full_name"] == "Ali V."
    assert updated["username"] == "ali_new"


async def test_admin_ids_are_flagged(database) -> None:
    admin = await users.ensure_user(111, "Admin", "admin")
    assert admin["is_admin"] == 1


async def test_phone_and_terms_verification(database) -> None:
    user = await users.ensure_user(2002, "Buyer", None)
    await users.set_phone(user["id"], "+998901234567")
    await users.accept_terms(user["id"])
    fresh = await users.get_user(user["id"])
    assert fresh["phone"] == "+998901234567"
    assert fresh["phone_verified"] == 1
    assert fresh["terms_accepted"] == 1


async def test_register_seller_assigns_unique_code_and_verifies_phone(database) -> None:
    first = await users.ensure_user(2003, "Seller One", None)
    second = await users.ensure_user(2004, "Seller Two", None)

    seller_one = await users.register_seller(first["id"], "+998901111111")
    seller_two = await users.register_seller(second["id"], "+998902222222")

    assert seller_one["is_seller"] == 1
    assert seller_one["phone_verified"] == 1
    assert len(seller_one["seller_code"]) == 6
    assert seller_one["seller_code"] != seller_two["seller_code"]

    again = await users.register_seller(first["id"], "+998903333333")
    assert again["seller_code"] == seller_one["seller_code"]
    assert again["phone"] == seller_one["phone"]


async def test_set_lang_validates_supported_codes(database) -> None:
    from services.errors import ValidationError

    user = await users.ensure_user(2005, "Lang User", None)
    await users.set_lang(user["id"], "ru")
    fresh = await users.get_user(user["id"])
    assert fresh["lang"] == "ru"

    try:
        await users.set_lang(user["id"], "xx")
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid language must raise ValidationError")
