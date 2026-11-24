from handoff_bench.grading.invoice_text import reports_unpaid_as_paid


def test_warning_against_retry_is_not_an_unpaid_invoice_claim() -> None:
    assert not reports_unpaid_as_paid(
        {"INV-889", "INV-205"},
        ["INV-147 is paid."],
        "Check for pending payment attempts on INV-889 or INV-205 so they aren't charged twice.",
    )
    assert reports_unpaid_as_paid({"INV-889"}, [], "INV-889 was charged twice.")


def test_negated_claim_does_not_mask_another_invoice() -> None:
    assert reports_unpaid_as_paid({"INV-999"}, [], "INV-999 was paid, and INV-111 wasn't paid.")
