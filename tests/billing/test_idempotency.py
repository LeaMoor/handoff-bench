import pytest

from handoff_bench.billing import BillingService, IdempotencyError


def charge(bank: BillingService, key: str | None = None, amount: int = 12000):
    return bank.create_charge("INV-12", "cus_ann", amount, key, actor="sub")


def test_same_key_same_params_returns_previous_result(bank: BillingService) -> None:
    first = charge(bank, key="k1")
    second = charge(bank, key="k1")

    assert second == first
    assert len(bank.journal.rows) == 1


def test_same_key_other_params_is_idempotency_error(bank: BillingService) -> None:
    charge(bank, key="k1")

    with pytest.raises(IdempotencyError):
        charge(bank, key="k1", amount=999)
    assert len(bank.journal.rows) == 1


def test_no_key_every_call_is_new_charge(bank: BillingService) -> None:
    first = charge(bank)
    second = charge(bank)

    assert first.charge_id != second.charge_id
    assert len(bank.journal.rows) == 2


def test_key_reused_for_other_invoice_is_error(bank: BillingService) -> None:
    charge(bank, key="k1")

    with pytest.raises(IdempotencyError):
        bank.create_charge("INV-13", "cus_bob", 4500, "k1", actor="sub")
