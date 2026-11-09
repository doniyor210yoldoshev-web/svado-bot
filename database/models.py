"""Domain constants shared across the application."""

from __future__ import annotations

from enum import StrEnum


class Lang(StrEnum):
    UZ = "uz"
    RU = "ru"
    EN = "en"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    SOLD = "sold"
    REFUNDED = "refunded"


class OrderStatus(StrEnum):
    CHECKING = "checking"
    COMPLETED = "completed"
    AUTO_COMPLETED = "auto_completed"
    DISPUTED = "disputed"
    REFUNDED = "refunded"


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DisputeStatus(StrEnum):
    PENDING = "pending"
    REFUNDED = "refunded"
    RELEASED = "released"


class Mode(StrEnum):
    REAL = "real"
    TEST = "test"


class TransactionType(StrEnum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    PENALTY = "penalty"
    SALE = "sale"
    COMMISSION = "commission"
    WITHDRAW = "withdraw"
    WITHDRAW_REFUND = "withdraw_refund"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


ACCOUNT_STATUSES = tuple(status.value for status in AccountStatus)
ORDER_STATUSES = tuple(status.value for status in OrderStatus)
