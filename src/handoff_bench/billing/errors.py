class BillingError(Exception):
    code: str = "billing_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __eq__(self, other: object) -> bool:
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
    code = "idempotency_error"


class BankTimeout(BillingError):
    code = "timeout"

    def __init__(self) -> None:
        # Fixed text: the agent gets the same message in the "went through" and "did not" worlds.
        super().__init__("Request to billing timed out; the outcome is unknown.")
