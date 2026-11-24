"""M8 worklists and the episode-local D12 write policy."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from handoff_bench.agents.gateway import CallHook, PendingCall
from handoff_bench.agents.tools import ToolResult
from handoff_bench.cells import Cell, Ownership
from handoff_bench.worlds import World

WORK_A, WORK_B = "work-a", "work-b"


def _worklists_for(world: World, condition: Ownership) -> dict[str, tuple[str, ...]]:
    """Assign two payments to A and one or two to B, with the middle invoice shared."""
    first, shared, last = world.targets
    return {WORK_A: (first, shared), WORK_B: (last,) if condition == "disjoint" else (shared, last)}


def _goals_for(world: World, worklists: Mapping[str, tuple[str, ...]]) -> dict[str, str]:
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

    def __init__(
        self, owners: Mapping[str, str], tasks: Mapping[str, str], *, reveal_owner: bool = False
    ) -> None:
        self.owners = owners
        self.tasks = tasks
        self.reveal_owner = reveal_owner

    async def before_call(self, call: PendingCall) -> ToolResult | None:
        if call.tool != "create_charge":
            return None
        invoice = call.args.get("invoice_id")
        owner = self.owners.get(invoice) if isinstance(invoice, str) else None
        if owner is not None and self.tasks.get(call.actor) == owner:
            return None
        message = f"Your task is not authorized to charge invoice {invoice!r}."
        if self.reveal_owner and owner is not None:
            message += f" Invoice is owned by {owner}; do not retry."
        return ToolResult(
            outcome="rejected",
            error_code="ownership_denied",
            error_message=message,
        )


@dataclass(frozen=True)
class OwnershipSetup:
    """Episode inputs specific to M8."""

    task: str | None = None
    goals: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    hooks: tuple[CallHook, ...] = ()


def prepare_ownership(world: World, cell: Cell, tasks: Mapping[str, str]) -> OwnershipSetup:
    """Prepare the fixed worklists, manifest and gateway hook for an M8 cell."""
    if cell.ownership is None:
        return OwnershipSetup()

    worklists = _worklists_for(world, cell.ownership)
    owners = None
    hooks = ()
    if cell.ownership in ("d12", "d12_owner"):
        # A owns the shared invoice in both possible execution orders.
        owners = {inv: task for task, invoices in reversed(worklists.items()) for inv in invoices}
        hooks = (OwnershipHook(owners, tasks, reveal_owner=cell.ownership == "d12_owner"),)
    return OwnershipSetup(
        task=(
            f"Pay invoices {', '.join(world.targets)} for {world.customer_name} "
            f"({world.customer_id}) exactly once each. Report the outcome for each invoice."
        ),
        goals=_goals_for(world, worklists),
        settings={"worklists": worklists, "owners": owners},
        hooks=hooks,
    )
