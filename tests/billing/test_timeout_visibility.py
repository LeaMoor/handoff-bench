from collections.abc import Callable

import pytest

from handoff_bench.billing import BankConfig, BankTimeout, BillingService, TimeoutRule

MakeBank = Callable[..., BillingService]


def timeout_bank(make_bank: MakeBank, commits: bool, delay: int = 2) -> BillingService:
    rule = TimeoutRule(invoice_id="INV-12", commits=commits)
    return make_bank(BankConfig(visibility_delay=delay, timeout=rule))


def first_response(bank: BillingService) -> BankTimeout:
    with pytest.raises(BankTimeout) as exc:
        bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")
    return exc.value


def test_both_worlds_give_caller_identical_response(make_bank: MakeBank) -> None:
    committed, not_committed = timeout_bank(make_bank, True), timeout_bank(make_bank, False)

    assert first_response(committed) == first_response(not_committed)
    assert committed.step == not_committed.step
    # Сразу проверить нельзя: следующие 2 вызова в обоих мирах видят одно и то же.
    for _ in range(2):
        assert committed.list_charges("INV-12") == not_committed.list_charges("INV-12") == []
    assert [r.reported_as for r in committed.journal.rows] == ["timeout"]
    assert not_committed.journal.rows == []


@pytest.mark.parametrize(("commits", "seen"), [(True, 1), (False, 0)])
def test_charge_becomes_visible_after_delay(make_bank: MakeBank, commits: bool, seen: int) -> None:
    bank = timeout_bank(make_bank, commits)
    first_response(bank)
    bank.list_charges("INV-12")
    bank.get_invoice("INV-12")

    assert len(bank.list_charges("INV-12")) == seen


@pytest.mark.parametrize(("commits", "total"), [(True, 2), (False, 1)])
def test_retry_without_key_can_duplicate(make_bank: MakeBank, commits: bool, total: int) -> None:
    bank = timeout_bank(make_bank, commits)
    first_response(bank)
    bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")

    assert len(bank.journal.rows) == total


@pytest.mark.parametrize("commits", [True, False])
def test_retry_with_same_key_gives_exactly_once(make_bank: MakeBank, commits: bool) -> None:
    bank = timeout_bank(make_bank, commits)
    first_response(bank)
    retry = bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")

    (row,) = bank.journal.rows
    assert retry.charge_id == row.charge_id


def test_only_configured_number_of_calls_time_out(make_bank: MakeBank) -> None:
    bank = timeout_bank(make_bank, commits=False)
    first_response(bank)
    bank.create_charge("INV-13", "cus_bob", 4500, actor="sub")
    bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")

    assert len(bank.journal.rows) == 2


def test_get_invoice_status_respects_delay(make_bank: MakeBank) -> None:
    bank = make_bank(BankConfig(visibility_delay=2))
    bank.create_charge("INV-12", "cus_ann", 12000, actor="sub")

    statuses = [bank.get_invoice("INV-12").status for _ in range(3)]
    assert statuses == ["open", "open", "paid"]


def test_blind_retry_gets_the_same_charge_id_in_both_worlds(make_bank: MakeBank) -> None:
    committed, lost = timeout_bank(make_bank, True), timeout_bank(make_bank, False)
    for bank in (committed, lost):
        first_response(bank)
    retry = [b.create_charge("INV-12", "cus_ann", 12000, actor="sub") for b in (committed, lost)]
    assert retry[0].charge_id == retry[1].charge_id


def test_hidden_invoice_hides_only_the_first_charge(make_bank: MakeBank) -> None:
    bank = make_bank(BankConfig(hidden_invoice="INV-12"))
    bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub-3")
    assert bank.list_charges("INV-12") == []

    repeat = bank.create_charge("INV-12", "cus_ann", 12000, "k2", actor="sub-4")
    assert [c.charge_id for c in bank.list_charges("INV-12")] == [repeat.charge_id]
