"""Faults on the subagent's payment of the focal invoice.

pre:  the call is not executed and the subagent stops.
post: the call goes through and the subagent finishes, but its report is lost.
Either way the orchestrator gets the same empty `delegate` result. A fault fires at most
once per episode, so a re-delegation runs normally."""

from typing import Literal

from handoff_bench.agents.gateway import CallHook, PendingCall
from handoff_bench.agents.tools import ToolResult

FaultMode = Literal["none", "pre", "post"]


class Injector(CallHook):
    def __init__(self, mode: FaultMode, focal_invoice: str) -> None:
        self.mode = mode
        self.focal_invoice = focal_invoice
        self.fired_on: str | None = None  # actor of the broken subagent

    @property
    def fired(self) -> bool:
        return self.fired_on is not None

    def report_lost(self, actor: str) -> bool:
        return self.mode == "post" and self.fired_on == actor

    def _hits(self, call: PendingCall) -> bool:
        return (
            not self.fired
            and call.tool == "create_charge"
            and call.args.get("invoice_id") == self.focal_invoice
        )

    async def before_call(self, call: PendingCall) -> ToolResult | None:
        if self.mode == "pre" and self._hits(call):
            self.fired_on = call.actor
            return ToolResult(outcome="dropped")
        return None

    async def after_call(self, call: PendingCall, result: ToolResult) -> ToolResult:
        if self.mode == "post" and result.outcome == "ok" and self._hits(call):
            self.fired_on = call.actor
        return result
