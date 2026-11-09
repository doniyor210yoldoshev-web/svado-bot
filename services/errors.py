"""Business exceptions that are safe to show to end users."""

from __future__ import annotations

from utils import fmt_sum


class ServiceError(Exception):
    """A business rule violation shown to the user (already user-facing text)."""


class ValidationError(ServiceError):
    pass


class InsufficientFunds(ServiceError):
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(
            f"Balans yetarli emas. Kerak: {fmt_sum(required)}, mavjud: {fmt_sum(available)}"
        )


class NotFound(ServiceError):
    pass


class PermissionDenied(ServiceError):
    pass
