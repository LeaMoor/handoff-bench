"""Ошибки банка. У каждой есть `code` — машинный код, который увидит агент."""


class BillingError(Exception):
    code: str = "billing_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __eq__(self, other: object) -> bool:
        # Нужно для тестов эквивалентности: два ответа банка совпадают, если совпали код и текст.
        if not isinstance(other, BillingError):
            return NotImplemented
        return (self.code, self.message) == (other.code, other.message)

    def __hash__(self) -> int:
        return hash((self.code, self.message))


class InvoiceNotFound(BillingError):
    code = "invoice_not_found"


class CustomerMismatch(BillingError):
    code = "customer_mismatch"


class InvalidAmount(BillingError):
    code = "invalid_amount"


class IdempotencyError(BillingError):
    """Тот же ключ идемпотентности, но другие параметры запроса."""

    code = "idempotency_error"


class BankTimeout(BillingError):
    """Банк не ответил. Списание при этом могло пройти, а могло и нет."""

    code = "timeout"

    def __init__(self) -> None:
        # Текст фиксирован: в мирах «прошло» и «не прошло» агент получает одно и то же.
        super().__init__("Request to billing timed out; the outcome is unknown.")
