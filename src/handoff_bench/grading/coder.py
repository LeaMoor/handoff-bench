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


def code_behaviour(
    calls: list[CallRow], *, orchestrator: str, responses: list[int], final_text: str | None
) -> Behaviour:
    """`responses`: how many tool calls each of the orchestrator's model responses made.

    Every tool call leaves exactly one journal row, so the orchestrator's rows, in `seq` order,
    split into responses. The reaction to the report is what the responses after the one with
    the first accepted `delegate` did: calls batched with that `delegate` were decided before
    the report was read. Only accepted calls count."""
    own = sorted((r for r in calls if r.actor == orchestrator), key=lambda r: r.seq)
    batches, start = [], 0
    for n in responses:
        batches.append(own[start : start + n])
        start += n
    first = next(
        (i for i, b in enumerate(batches) if any(_delegated(r) for r in b)),
        None,
    )
    if first is None:
        return Behaviour(first_action="none", decision="none", self_checked=False)
    after = [_action(r) for b in batches[first + 1 :] for r in b if r.outcome == "ok"]
    tail: Action = "none"
    if final_text is not None:
        tail = "ask_user" if "?" in final_text else "other"
    return Behaviour(
        first_action=after[0] if after else tail,
        decision=next((a for a in after if a != "check"), tail),
        self_checked="check" in after,
    )


def _delegated(row: CallRow) -> bool:
    return row.tool == "delegate" and row.outcome == "ok"
