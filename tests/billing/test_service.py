"""Базовое поведение: проверки, статус счёта, часы, журнал, воспроизводимость."""

from collections.abc import Callable

import pytest

from handoff_bench.billing import (
    BillingService,
    CustomerMismatch,
    EffectJournal,
    InvalidAmount,
    Invoice,
    InvoiceNotFound,
)
from handoff_bench.billing.journal import dump_row

MakeBank = Callable[..., BillingService]


def test_get_invoice_open_then_paid(bank: BillingService) -> None:
    assert bank.get_invoice("INV-12").status == "open"
    bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")
    assert bank.get_invoice("INV-12").status == "paid"


def test_list_charges_shows_only_this_invoice(bank: BillingService) -> None:
    ch = bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")
    bank.create_charge("INV-13", "cus_bob", 4500, actor="sub")

    assert bank.list_charges("INV-12") == [ch]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (("INV-99", "cus_ann", 100), InvoiceNotFound),
        (("INV-12", "cus_bob", 12000), CustomerMismatch),
        (("INV-12", "cus_ann", 0), InvalidAmount),
    ],
)
def test_create_charge_validation(bank: BillingService, args: tuple, error: type) -> None:
    with pytest.raises(error):
        bank.create_charge(*args, actor="sub")
    assert bank.journal.rows == []


def test_every_call_ticks_clock_even_on_error(bank: BillingService) -> None:
    bank.get_invoice("INV-12")
    with pytest.raises(InvoiceNotFound):
        bank.list_charges("INV-99")
    bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")

    assert bank.step == 3
    assert bank.journal.rows[0].step == 3


def test_effect_row_records_actor_and_key(bank: BillingService) -> None:
    ch = bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="orchestrator")

    (row,) = bank.journal.rows
    assert (row.actor, row.idempotency_key, row.charge_id) == ("orchestrator", "k1", ch.charge_id)
    assert row.reported_as == "succeeded"


def test_journal_file_roundtrip_and_stable_format(tmp_path) -> None:
    path = tmp_path / "ep" / "effects.jsonl"
    bank = BillingService(episode_id="ep-1", journal=EffectJournal(path))
    bank.add_invoices([Invoice(invoice_id="INV-12", customer_id="cus_ann", amount=12000)])
    bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")

    rows = EffectJournal.read(path)
    assert rows == bank.journal.rows
    line = path.read_text(encoding="utf-8").strip()
    assert line == dump_row(rows[0])
    assert line.startswith('{"actor":"sub","amount":12000,')  # ключи отсортированы, без пробелов


def test_charge_ids_reproducible_by_seed(make_bank: MakeBank) -> None:
    def ids(seed: int) -> list[str]:
        bank = make_bank(id_seed=seed)
        return [
            bank.create_charge("INV-12", "cus_ann", 100, actor="sub").charge_id for _ in range(3)
        ]

    assert ids(7) == ids(7)
    assert ids(7) != ids(8)
