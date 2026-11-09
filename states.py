"""FSM state groups."""

from aiogram.fsm.state import State, StatesGroup


class SellerFlow(StatesGroup):
    phone = State()
    title = State()
    description = State()
    login = State()
    password = State()
    price = State()
    confirm = State()


class PriceEdit(StatesGroup):
    value = State()


class DisputeFlow(StatesGroup):
    reason = State()


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
