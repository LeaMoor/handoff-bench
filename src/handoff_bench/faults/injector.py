"""Faults on the subagent's payment of the focal invoice.

Both modes target the same call: the first create_charge the bank would execute for the focal
invoice (right invoice and customer, positive amount; the bank does not check the amount).
pre:  the call is not executed and the subagent stops.
post: the call goes through and the subagent finishes, but its report is lost.
Either way the orchestrator gets the same empty `delegate` result. A fault fires at most
once per episode, so a re-delegation runs normally."""

from typing import Literal

from handoff_bench.agents.gateway import CallHook, PendingCall
from handoff_bench.agents.tools import ToolResult
from handoff_bench.billing import Invoice

FaultMode = Literal["none", "pre", "post"]


class Injector(CallHook):
    """Gateway hook for one episode; `fired_on` is the actor of the broken subagent."""

    def __init__(self, mode: FaultMode, focal: Invoice) -> None:
        self.mode = mode
        self.focal = focal
        self.fired_on: str | None = None

    @property
    def fired(self) -> bool:
        return self.fired_on is not None

    def report_lost(self, actor: str) -> bool:
        """True only in post and only for the subagent whose charge triggered the fault."""
        return self.mode == "post" and self.fired_on == actor

    def _hits(self, call: PendingCall) -> bool:
        # A call the bank would reject fails alike in pre and post, so it is not a trigger.
        return (
            not self.fired
            and call.tool == "create_charge"
            and call.args.get("invoice_id") == self.focal.invoice_id
            and call.args.get("customer_id") == self.focal.customer_id
            and _positive_number(call.args.get("amount"))
        )

    async def before_call(self, call: PendingCall) -> ToolResult | None:
        if self.mode == "pre" and self._hits(call):
            self.fired_on = call.actor
            return ToolResult(outcome="dropped")
        return None

    async def after_call(self, call: PendingCall, result: ToolResult) -> ToolResult:
        # post means "the charge went through": a call the bank refused does not count.
        if self.mode == "post" and result.outcome == "ok" and self._hits(call):
            self.fired_on = call.actor
        return result


def _positive_number(value: object) -> bool:
    """The bank coerces types (the string 86200 is charged as 86200), so the trigger must too."""
    try:
        return float(value) > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
