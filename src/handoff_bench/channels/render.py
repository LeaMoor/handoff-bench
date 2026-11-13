"""What the orchestrator gets back from `delegate`, per report channel.

text: the subagent's own words (C-text).
code: a statement the code builds from the subagent's call journal (C-code) — never from the
      effect journal, which only the grader reads.
"""

from typing import Literal

from handoff_bench.agents.call_journal import CallRow
from handoff_bench.agents.loop import AgentTrace

Channel = Literal["text", "code"]

HEADER = "Subagent's bank calls, from the call log:"
CHECKS = ("get_invoice", "list_charges")


def render(
    channel: Channel, child: AgentTrace, calls: list[CallRow], *, report_lost: bool = False
) -> str:
    """The `result` string for one subagent run; `calls` are that subagent's journal rows."""
    if channel == "text":
        return "" if report_lost else subagent_words(child)
    return statement(calls)


def subagent_words(child: AgentTrace) -> str:
    """The `finish_report` text, else the subagent's last plain answer, else nothing."""
    if child.finish_tool == "finish_report" and child.finish_args is not None:
        return child.finish_args["text"]
    if child.stop_reason == "text_only":
        return child.messages[-1].get("content") or ""
    return ""


def statement(calls: list[CallRow]) -> str:
    lines = [HEADER]
    charges = 0
    for r in sorted(calls, key=lambda r: r.seq):
        # A call the injector intercepted never reached the bank: it reads as "no call",
        # otherwise the statement itself would reveal the fault.
        if r.tool not in ("create_charge", *CHECKS) or r.outcome in ("dropped", "infra_error"):
            continue
        charges += r.tool == "create_charge"
        lines.append(f"- step {r.seq}: {r.tool} {r.args.get('invoice_id', '?')}: {_outcome(r)}")
    if charges == 0:
        lines.append("- no create_charge calls")
    return "\n".join(lines)


def _outcome(r: CallRow) -> str:
    if r.outcome == "timeout":
        return "timeout, outcome unknown"
    if r.outcome != "ok":
        return f"error {r.error_code}"
    if r.tool == "create_charge":
        return f"success, charge {r.result['charge_id']}"
    if r.tool == "get_invoice":
        return f"status {r.result['status']}"
    ids = [c["charge_id"] for c in r.result]
    if not ids:
        return "no charges"
    return f"{len(ids)} charge{'s' if len(ids) > 1 else ''} ({', '.join(ids)})"
