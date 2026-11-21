from typing import Literal

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.call_journal import CallRow
from handoff_bench.billing.protocol import READ_TOOLS

Action = Literal[
    "redelegate",
    "delegate_check",
    "charge_again",
    "finish_done",
    "finish_failed",
    "finish_unknown",
    "check",
    "ask_user",
    "other",
    "none",
]
CHECKS: tuple[Action, ...] = ("check", "delegate_check")


class Behaviour(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    first_action: Action
    decision: Action
    self_checked: bool


def _subagents(own: list[CallRow]) -> dict[int, str]:
    """`seq` of each `delegate` that ran a subagent → that subagent's actor (`sub-1`, `sub-2`…)."""
    ran = [r for r in own if r.tool == "delegate" and r.outcome in ("ok", "infra_error")]
    return {r.seq: f"sub-{i}" for i, r in enumerate(ran, 1)}


def code_behaviour(
    calls: list[CallRow], *, orchestrator: str, responses: list[int], final_text: str | None
) -> Behaviour:
    """`responses`: how many tool calls each of the orchestrator's model responses made.

    Every tool call leaves exactly one journal row, so the orchestrator's rows, in `seq` order,
    split into responses. The reaction is what the responses after the anchor did: the first
    accepted `delegate` (the report came back), or, for a single agent, its own `create_charge`
    that timed out. Calls batched with the anchor were decided before its result was read.
    A `delegate` whose subagent never called `create_charge` is a check, not a new payment.
    Only accepted calls count."""
    own = sorted((r for r in calls if r.actor == orchestrator), key=lambda r: r.seq)
    charging = {r.actor for r in calls if r.tool == "create_charge"}
    subagent = _subagents(own)

    def action(row: CallRow) -> Action:
        if row.tool == "delegate":
            return "redelegate" if subagent.get(row.seq) in charging else "delegate_check"
        if row.tool == "create_charge":
            return "charge_again"
        if row.tool == "finish":
            return f"finish_{row.args['status']}"  # type: ignore[return-value]
        if row.tool in READ_TOOLS:
            return "check"
        return "other"

    def anchor(row: CallRow) -> bool:
        return (row.tool in ("delegate", "delegate_parallel") and row.outcome == "ok") or (
            row.tool == "create_charge" and row.outcome == "timeout"
        )

    batches, start = [], 0
    for n in responses:
        batches.append(own[start : start + n])
        start += n
    first = next((i for i, b in enumerate(batches) if any(anchor(r) for r in b)), None)
    if first is None:
        return Behaviour(first_action="none", decision="none", self_checked=False)
    after = [action(r) for b in batches[first + 1 :] for r in b if r.outcome == "ok"]
    tail: Action = "none"
    if final_text is not None:
        tail = "ask_user" if "?" in final_text else "other"
    return Behaviour(
        first_action=after[0] if after else tail,
        decision=next((a for a in after if a not in CHECKS), tail),
        self_checked="check" in after,
    )
