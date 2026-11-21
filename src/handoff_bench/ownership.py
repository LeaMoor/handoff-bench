"""M8 worklists and the episode-local D12 write policy."""

from collections.abc import Mapping

from handoff_bench.agents.gateway import CallHook, PendingCall
from handoff_bench.agents.tools import ToolResult
from handoff_bench.cells import Ownership
from handoff_bench.worlds import World

WORK_A, WORK_B = "work-a", "work-b"


def worklists_for(world: World, condition: Ownership) -> dict[str, tuple[str, ...]]:
    """Assign two payments to A and one or two to B, with the focal invoice shared."""
    first, shared, last = world.targets
    return {WORK_A: (first, shared), WORK_B: (last,) if condition == "disjoint" else (shared, last)}


def goals_for(world: World, worklists: Mapping[str, tuple[str, ...]]) -> dict[str, str]:
    """Give each worker its invoice IDs and the same customer information."""
    return {
        task: (
            f"Pay invoices {', '.join(invoice_ids)} for {world.customer_name} "
            f"({world.customer_id}). Report the outcome for each assigned invoice."
        )
        for task, invoice_ids in worklists.items()
    }


class OwnershipHook(CallHook):
    """Allow charges only for invoices owned by the caller's task. Reads stay available."""

    def __init__(self, owners: Mapping[str, str], tasks: Mapping[str, str]) -> None:
        self.owners = owners
        self.tasks = tasks

    async def before_call(self, call: PendingCall) -> ToolResult | None:
        if call.tool != "create_charge":
            return None
        invoice = call.args.get("invoice_id")
        owner = self.owners.get(invoice) if isinstance(invoice, str) else None
        if owner is not None and self.tasks.get(call.actor) == owner:
            return None
        return ToolResult(
            outcome="rejected",
            error_code="ownership_denied",
            error_message=f"Your task is not authorized to charge invoice {invoice!r}.",
        )
