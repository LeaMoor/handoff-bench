"""How a fault breaks an episode: bank settings plus gateway hooks (tasks 8, 15).

To add a fault, add an entry to FAULTS; `cells.py` already names all of them."""

from collections.abc import Callable
from dataclasses import dataclass, field

from handoff_bench.agents.gateway import CallHook
from handoff_bench.billing.models import BankConfig
from handoff_bench.cells import Fault
from handoff_bench.worlds import World


@dataclass(frozen=True)
class FaultSetup:
    bank: BankConfig = field(default_factory=BankConfig)
    hooks: tuple[CallHook, ...] = ()


def no_fault(world: World) -> FaultSetup:
    return FaultSetup()


FAULTS: dict[Fault, Callable[[World], FaultSetup]] = {"none": no_fault}
