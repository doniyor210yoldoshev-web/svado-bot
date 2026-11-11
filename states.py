"""FSM state groups."""

from aiogram.fsm.state import State, StatesGroup


class RegistrationFlow(StatesGroup):
    phone = State()


class SellerFlow(StatesGroup):
    phone = State()
    title = State()
    description = State()
    price = State()
    photo = State()
    extra_info = State()
    login = State()
    password = State()
    confirm = State()
    verify_ownership = State()


class PriceEdit(StatesGroup):
    value = State()


class DisputeFlow(StatesGroup):
    reason = State()
    evidence = State()


class TopupFlow(StatesGroup):
    amount = State()
    receipt = State()


class WithdrawFlow(StatesGroup):
    amount = State()
    card = State()


class SearchFlow(StatesGroup):
    query = State()


class AdminFlow(StatesGroup):
    user_query = State()
    balance_amount = State()
    balance_note = State()
    broadcast_text = State()
