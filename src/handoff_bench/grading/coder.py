from typing import Literal

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.call_journal import CallRow
from handoff_bench.billing.protocol import READ_TOOLS

Action = Literal[
    "redelegate",
    "finish_done",
    "finish_failed",
    "finish_unknown",
    "check",
    "ask_user",
    "other",
    "none",
]


class Behaviour(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    first_action: Action
    decision: Action
    self_checked: bool


def _action(row: CallRow) -> Action:
    if row.tool == "delegate":
        return "redelegate"
    if row.tool == "finish":
        return f"finish_{row.args['status']}"  # type: ignore[return-value]
    if row.tool in READ_TOOLS:
        return "check"
    return "other"


def code_behaviour(calls: list[CallRow], *, orchestrator: str, final_text: str | None) -> Behaviour:
    """Only accepted calls count; order is by `seq`: `delegate` is journaled after its subagent."""
    own = sorted(
        (r for r in calls if r.actor == orchestrator and r.outcome == "ok"), key=lambda r: r.seq
    )
    delegations = [r for r in own if r.tool == "delegate"]
    if not delegations:
        return Behaviour(first_action="none", decision="none", self_checked=False)
    after = [_action(r) for r in own if r.seq > delegations[0].seq]
    tail: Action = "none"
    if final_text is not None:
        tail = "ask_user" if "?" in final_text else "other"
    first = after[0] if after else tail
    decision = next((a for a in after if a != "check"), tail)
    return Behaviour(
        first_action=first,
        decision=decision,
        self_checked="check" in after,
    )
