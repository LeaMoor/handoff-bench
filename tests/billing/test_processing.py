from collections.abc import Callable

import pytest

from handoff_bench.billing import BankConfig, BillingService, ProcessingRule

MakeBank = Callable[..., BillingService]


def processing_bank(make_bank: MakeBank, settles: bool) -> BillingService:
    return make_bank(BankConfig(processing=ProcessingRule(invoice_id="INV-12", settles=settles)))


def both(make_bank: MakeBank) -> tuple[BillingService, BillingService]:
    return processing_bank(make_bank, True), processing_bank(make_bank, False)


def test_both_worlds_answer_processing_and_list_it_alike(make_bank: MakeBank) -> None:
    settles, fails = both(make_bank)

    first = [
        b.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub") for b in (settles, fails)
    ]
    assert first[0] == first[1]
    assert first[0].status == "processing"
    assert settles.list_charges("INV-12") == fails.list_charges("INV-12") == [first[0]]
    # A processing charge does not pay the invoice yet.
    assert settles.get_invoice("INV-12").status == fails.get_invoice("INV-12").status == "open"
    assert [r.reported_as for r in settles.journal.rows] == ["processing"]
    assert fails.journal.rows == []


def test_retry_with_the_same_key_returns_the_same_processing_charge(make_bank: MakeBank) -> None:
    for bank in both(make_bank):
        first = bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")
        assert bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub") == first


@pytest.mark.parametrize(("settles", "effects"), [(True, 2), (False, 1)])
def test_retry_without_a_key_charges_again(
    make_bank: MakeBank, settles: bool, effects: int
) -> None:
    bank = processing_bank(make_bank, settles)
    bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")
    second = bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")

    assert second.status == "succeeded"
    assert len(bank.journal.rows) == effects


def test_processing_charge_keeps_the_bank_visibility_rules(make_bank: MakeBank) -> None:
    # The hidden invoice hides its first charge, the processing one; processing must not punch
    # a hole in that. A repayment shows as usual, in both worlds.
    for settles in (True, False):
        bank = make_bank(
            BankConfig(
                processing=ProcessingRule(invoice_id="INV-12", settles=settles),
                hidden_invoice="INV-12",
            )
        )
        bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")
        repayment = bank.create_charge("INV-12", "cus_ann", 12000, "k2", actor="sub")
        assert bank.list_charges("INV-12") == [repayment]
