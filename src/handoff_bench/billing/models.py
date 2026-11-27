from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Invoice(_Frozen):
    invoice_id: str
    customer_id: str
    amount: int = Field(gt=0)
    description: str = ""


class InvoiceView(_Frozen):
    invoice_id: str
    customer_id: str
    amount: int
    description: str
    # Status respects the visibility delay, so get_invoice cannot show payment before the statement.
    status: Literal["open", "paid"]


class Capture(_Frozen):
    capture_id: str
    status: Literal["completed", "declined"]


class ChargeView(_Frozen):
    charge_id: str
    invoice_id: str
    customer_id: str
    amount: int
    # processing: accepted, final outcome not known yet (Stripe `processing`, Adyen `Pending`).
    # completed: the order went through; whether money moved is in `captures` (PayPal orders).
    status: Literal["succeeded", "processing", "completed"] = "succeeded"
    # Only on a `completed` charge: other charges keep their old shape, byte for byte.
    captures: list[Capture] | None = Field(default=None, exclude_if=lambda v: v is None)


class TimeoutRule(_Frozen):
    invoice_id: str
    calls: int = Field(default=1, ge=1)
    # True: the "charge went through" world; False: "did not". The caller gets a timeout in both.
    commits: bool
    # A late commit: the committed charge never shows in list_charges / get_invoice status.
    hidden: bool = False


class ProcessingRule(_Frozen):
    """The first charge on the invoice is accepted as `processing` in both worlds and settles
    (True) or fails (False) only after the episode: same id, same listing, same key replay."""

    invoice_id: str
    settles: bool


class CaptureRule(_Frozen):
    """M3: the first charge on the invoice comes back as a `completed` order in both worlds;
    its capture is `completed` (paid) or `declined` (not paid), as PayPal orders do."""

    invoice_id: str
    declined: bool


class BankConfig(_Frozen):
    visibility_delay: int = Field(default=0, ge=0)
    timeout: TimeoutRule | None = None
    processing: ProcessingRule | None = None
    capture: CaptureRule | None = None
    # The first charge on this invoice never shows to checks — the one the fault hit
    # (calibration without the loophole, M4). Later charges, a repayment too, show as usual.
    hidden_invoice: str | None = None


ReportedAs = Literal["succeeded", "timeout", "processing", "completed"]


class EffectRow(_Frozen):
    episode_id: str
    step: int
    invoice_id: str
    customer_id: str
    amount: int
    charge_id: str
    idempotency_key: str | None
    actor: str
    reported_as: ReportedAs
