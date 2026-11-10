"""Данные банка. Суммы — целые, в минимальных единицах (центах), как у Stripe."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- То, что кладёт в банк мир -------------------------------------------------------------


class Invoice(_Frozen):
    invoice_id: str
    customer_id: str
    amount: int = Field(gt=0)
    description: str = ""


# --- То, что банк отдаёт агентам -----------------------------------------------------------


class InvoiceView(_Frozen):
    invoice_id: str
    customer_id: str
    amount: int
    description: str
    # paid — когда видимые списания покрывают сумму счёта (с учётом задержки видимости).
    status: Literal["open", "paid"]


class ChargeView(_Frozen):
    charge_id: str
    invoice_id: str
    customer_id: str
    amount: int
    status: Literal["succeeded"] = "succeeded"


# --- Настройки банка на эпизод ---------------------------------------------------------------


class TimeoutRule(_Frozen):
    """Первые `calls` вызовов create_charge по счёту `invoice_id` отвечают таймаутом."""

    invoice_id: str
    calls: int = Field(default=1, ge=1)
    # True — списание при таймауте всё равно записывается (мир «прошло»), False — нет.
    commits: bool


class BankConfig(_Frozen):
    # Сколько следующих вызовов банка ещё НЕ видят новое списание (в list_charges и get_invoice).
    # 0 — видно уже на следующем вызове; 2 — два следующих вызова не видят, третий видит.
    visibility_delay: int = Field(default=0, ge=0)
    timeout: TimeoutRule | None = None


# --- Журнал эффектов (только для грейдера) ---------------------------------------------------


class EffectRow(_Frozen):
    episode_id: str
    step: int
    invoice_id: str
    customer_id: str
    amount: int
    charge_id: str
    idempotency_key: str | None
    actor: str
    # Ответ, который получил вызывающий: обычный успех или таймаут (списание при этом есть).
    reported_as: Literal["succeeded", "timeout"]
