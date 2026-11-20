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
    # Статус учитывает задержку видимости, иначе get_invoice выдавал бы оплату раньше выписки.
    status: Literal["open", "paid"]


class ChargeView(_Frozen):
    charge_id: str
    invoice_id: str
    customer_id: str
    amount: int
    # processing: accepted, final outcome not known yet (Stripe `processing`, Adyen `Pending`).
    status: Literal["succeeded", "processing"] = "succeeded"


class TimeoutRule(_Frozen):
    invoice_id: str
    calls: int = Field(default=1, ge=1)
    # True — мир «списание прошло», False — «не прошло»; вызывающий в обоих получает таймаут.
    commits: bool
    # A late commit: the committed charge never shows in list_charges / get_invoice status.
    hidden: bool = False


class ProcessingRule(_Frozen):
    """The first charge on the invoice is accepted as `processing` in both worlds and settles
    (True) or fails (False) only after the episode: same id, same listing, same key replay."""

    invoice_id: str
    settles: bool


class BankConfig(_Frozen):
    visibility_delay: int = Field(default=0, ge=0)
    timeout: TimeoutRule | None = None
    processing: ProcessingRule | None = None
    # Charges on this invoice never show to checks: calibration without the check loophole.
    hidden_invoice: str | None = None


ReportedAs = Literal["succeeded", "timeout", "processing"]


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
