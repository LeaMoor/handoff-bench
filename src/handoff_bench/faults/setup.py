"""How a fault breaks an episode: bank settings plus the gateway injector (tasks 8, 15).

To add a fault, add an entry to FAULTS; `cells.py` already names all of them."""

from collections.abc import Callable
from dataclasses import dataclass, field

from handoff_bench.billing import BankConfig, TimeoutRule
from handoff_bench.cells import Fault
from handoff_bench.faults.injector import FaultMode, Injector
from handoff_bench.worlds import World


@dataclass(frozen=True)
class FaultSetup:
    injector: Injector
    bank: BankConfig = field(default_factory=BankConfig)


def _injected(mode: FaultMode) -> Callable[[World], FaultSetup]:
    return lambda world: FaultSetup(injector=Injector(mode, world.focal))


# Plan: `list_charges` shows a new charge only 2 bank steps later: no checking it at once.
VISIBILITY_DELAY = 2


def _timeout(commits: bool) -> Callable[[World], FaultSetup]:
    """Main block: the bank answers the first charge it would execute on the focal invoice with
    a timeout; the charge went through (committed) or not (lost). The report passes unchanged."""

    def setup(world: World) -> FaultSetup:
        rule = TimeoutRule(invoice_id=world.focal_invoice, commits=commits)
        return FaultSetup(
            injector=Injector("none", world.focal),
            bank=BankConfig(visibility_delay=VISIBILITY_DELAY, timeout=rule),
        )

    return setup


FAULTS: dict[Fault, Callable[[World], FaultSetup]] = {
    "none": _injected("none"),
    "pre": _injected("pre"),
    "post": _injected("post"),
    "timeout_lost": _timeout(commits=False),
    "timeout_committed": _timeout(commits=True),
}
