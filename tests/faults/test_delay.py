import pytest
from helpers.episode import DEMO_WORLD

from handoff_bench.faults.setup import FAULTS, with_delay


@pytest.mark.parametrize("fault", list(FAULTS))
def test_short_delay_changes_nothing(fault: str) -> None:
    setup = FAULTS[fault](DEMO_WORLD)
    assert with_delay(setup, "short", DEMO_WORLD) is setup


@pytest.mark.parametrize("fault", ["timeout_lost", "timeout_committed"])
def test_long_delay_hides_only_the_timed_out_charge(fault: str) -> None:
    bank = with_delay(FAULTS[fault](DEMO_WORLD), "long", DEMO_WORLD).bank
    assert bank.timeout is not None and bank.timeout.hidden
    assert bank.hidden_invoice is None


def test_long_delay_hides_nothing_before_the_fault() -> None:
    # pre: nothing was charged before the lost report, so there is nothing to hide.
    setup = FAULTS["pre"](DEMO_WORLD)
    assert with_delay(setup, "long", DEMO_WORLD) is setup


@pytest.mark.parametrize("fault", ["post", "respawn", "redeliver"])
def test_long_delay_hides_the_first_focal_charge(fault: str) -> None:
    bank = with_delay(FAULTS[fault](DEMO_WORLD), "long", DEMO_WORLD).bank
    assert bank.hidden_invoice == DEMO_WORLD.focal_invoice
    assert bank.timeout is None
