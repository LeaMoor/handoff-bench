from collections.abc import Callable

from handoff_bench.billing import BankConfig, BillingService, CaptureRule

MakeBank = Callable[..., BillingService]


def capture_bank(make_bank: MakeBank, declined: bool) -> BillingService:
    return make_bank(BankConfig(capture=CaptureRule(invoice_id="INV-12", declined=declined)))


def test_both_worlds_answer_a_completed_order_and_differ_only_in_the_capture(
    make_bank: MakeBank,
) -> None:
    completed, declined = capture_bank(make_bank, False), capture_bank(make_bank, True)

    ok = completed.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")
    bad = declined.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")

    assert ok.status == bad.status == "completed"
    assert ok.charge_id == bad.charge_id
    assert [c.status for c in ok.captures] == ["completed"]
    assert [c.status for c in bad.captures] == ["declined"]
    assert ok.captures[0].capture_id == bad.captures[0].capture_id
    assert completed.list_charges("INV-12") == [ok]
    assert declined.list_charges("INV-12") == [bad]
    assert completed.get_invoice("INV-12").status == "paid"
    assert declined.get_invoice("INV-12").status == "open"
    assert [r.reported_as for r in completed.journal.rows] == ["completed"]
    assert declined.journal.rows == []


def test_a_plain_charge_keeps_its_old_shape(make_bank: MakeBank) -> None:
    bank = capture_bank(make_bank, True)
    plain = bank.create_charge("INV-13", "cus_bob", 4500, actor="sub")
    assert "captures" not in plain.model_dump(mode="json")


def test_retry_with_the_same_key_returns_the_same_declined_order(make_bank: MakeBank) -> None:
    bank = capture_bank(make_bank, True)
    first = bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")
    assert bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub") == first
    assert bank.journal.rows == []


def test_a_new_attempt_after_a_decline_is_a_plain_charge(make_bank: MakeBank) -> None:
    bank = capture_bank(make_bank, True)
    bank.create_charge("INV-12", "cus_ann", 12000, "k1", actor="sub")
    second = bank.create_charge("INV-12", "cus_ann", 12000, "k2", actor="sub")

    assert second.status == "succeeded"
    assert len(bank.journal.rows) == 1
    assert bank.get_invoice("INV-12").status == "paid"
