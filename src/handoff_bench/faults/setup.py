"""How a fault breaks an episode: bank settings plus the gateway injector (tasks 8, 15).

To add a fault, add an entry to FAULTS; `cells.py` already names all of them."""

from collections.abc import Callable
from dataclasses import dataclass, field

from handoff_bench.billing import BankConfig, Invoice
from handoff_bench.cells import Fault
from handoff_bench.faults.injector import FaultMode, Injector
from handoff_bench.worlds import World


@dataclass(frozen=True)
class FaultSetup:
    injector: Injector
    bank: BankConfig = field(default_factory=BankConfig)


def focal_invoice(world: World) -> Invoice:
    focal = next((inv for inv in world.invoices if inv.invoice_id == world.focal_invoice), None)
    if focal is None:
        raise ValueError(f"focal invoice {world.focal_invoice} is not among the world's invoices")
    return focal


def _injected(mode: FaultMode) -> Callable[[World], FaultSetup]:
    return lambda world: FaultSetup(injector=Injector(mode, focal_invoice(world)))


FAULTS: dict[Fault, Callable[[World], FaultSetup]] = {
    "none": _injected("none"),
    "pre": _injected("pre"),
    "post": _injected("post"),
}
