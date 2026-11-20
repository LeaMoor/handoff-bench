"""How a fault breaks an episode: bank settings plus the gateway injector (tasks 8, 15).

To add a fault, add an entry to FAULTS; `cells.py` already names all of them."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from handoff_bench.billing import BankConfig, TimeoutRule
from handoff_bench.cells import Delay, Fault
from handoff_bench.faults.injector import FaultMode, Injector
from handoff_bench.worlds import World


@dataclass(frozen=True)
class FaultSetup:
    injector: Injector
    bank: BankConfig = field(default_factory=BankConfig)
    deliveries: int = 1  # how many times the harness runs each delegation
    respawn_on_lost: bool = False  # a lost report makes the harness start a new subagent


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
    # M4: the system repeats the work, not the orchestrator.
    "respawn": lambda world: FaultSetup(
        injector=Injector("post", world.focal), respawn_on_lost=True
    ),
    "redeliver": lambda world: FaultSetup(injector=Injector("none", world.focal), deliveries=2),
}


def with_delay(setup: FaultSetup, delay: Delay, world: World) -> FaultSetup:
    """Long delay: a check reads "no charges" in both worlds.

    Timeout: the timed-out charge, if it went through, stays invisible (a late commit).
    Post (and M4): the first charge on the focal invoice, the one before the lost report, stays
    invisible, so a check delegated to a new subagent cannot tell it from pre, where that
    charge never happened (calibration without the loophole). A repayment shows as usual."""
    if delay == "short":
        return setup
    rule = setup.bank.timeout
    if rule is not None:
        update = {"timeout": rule.model_copy(update={"hidden": True})}
    elif setup.injector.mode == "pre":
        return setup  # nothing was charged before the fault: nothing to hide
    else:
        update = {"hidden_invoice": world.focal_invoice}
    return replace(setup, bank=setup.bank.model_copy(update=update))
