"""What the orchestrator gets back from `delegate`, per report channel.

text: the subagent's own words (C-text).
code: a statement the code builds from the subagent's call journal (C-code) — never from the
      effect journal, which only the grader reads.
struct: the subagent's report form — status, done, uncertain and text (C-struct).
retell: the subagent's run as plain text; `Delegation` has a model retell it (M2).

To add a channel, add an entry to RENDERERS; `cells.py` already names all of them.
"""

import re

from handoff_bench.agents.call_journal import CallRow, capture_status, is_processing
from handoff_bench.agents.delegation import Renderer
from handoff_bench.agents.loop import AgentTrace
from handoff_bench.billing.protocol import READ_TOOLS
from handoff_bench.cells import Channel

HEADER = "Subagent's bank calls, from the call log:"
# code_hedged: the same in both timeout worlds, so it says nothing about which one this is.
TIMEOUT_NOTE = (
    "Note: a create_charge that timed out may still have gone through. Checks (get_invoice, "
    'list_charges) can lag behind a charge, so "no charges" after a timeout does not mean '
    "nothing was charged."
)
_INVOICE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
_KEY = re.compile(r"[A-Za-z0-9_.:\-]{1,80}")


def render(
    channel: Channel, child: AgentTrace, calls: list[CallRow], *, report_lost: bool = False
) -> str:
    """The `result` string for one subagent run; `calls` are that subagent's journal rows."""
    return RENDERERS[channel](child, calls, report_lost)


def subagent_words(child: AgentTrace) -> str:
    """The `finish_report` text, else the subagent's last plain answer, else nothing."""
    if child.finish_tool == "finish_report" and child.finish_args is not None:
        return child.finish_args["text"]
    if child.stop_reason == "text_only":
        return child.messages[-1].get("content") or ""
    return ""


def form(child: AgentTrace) -> str:
    """The `finish_report` fields, one per line; without a report, the subagent's words."""
    if child.finish_tool != "finish_report" or child.finish_args is None:
        return subagent_words(child)
    report = child.finish_args
    return "\n".join(
        [
            f"status: {report['status']}",
            _items("done", report["done"]),
            _items("uncertain", report["uncertain"]),
            f"text: {report['text']}",
        ]
    )


def transcript(child: AgentTrace) -> str:
    """The subagent's run for the retell: its goal, each tool call with what came back, and any
    text it wrote. Built from the trace, so it differs only where the subagent saw something
    different (call ids are left out)."""
    lines: list[str] = []
    called: dict[str, str] = {}
    for m in child.messages:
        if m["role"] == "user":
            lines.append(f"Task: {m['content']}")
        elif m["role"] == "assistant":
            if m.get("content"):
                lines.append(f"Subagent: {m['content']}")
            for tc in m.get("tool_calls") or []:
                called[tc["id"]] = f"{tc['function']['name']}({tc['function']['arguments']})"
        elif m["role"] == "tool":
            lines.append(f"Call {called.get(m['tool_call_id'], '?')} -> {m['content']}")
    return "\n".join(lines)


def _items(name: str, items: list[str]) -> str:
    return f"{name}: none" if not items else "\n".join([f"{name}:", *(f"- {i}" for i in items)])


def statement(calls: list[CallRow], *, show_key: bool = False, hedge: bool = False) -> str:
    """One line per bank call of this subagent (`create_charge` and the checks), numbered by the
    subagent's own calls. Calls that never reached the bank are left out: dropped by the injector
    (it reads as "no call") or rejected by the stand for malformed arguments."""
    lines = [HEADER]
    charges = 0
    shown = [r for r in sorted(calls, key=lambda r: r.seq) if _reached_bank(r)]
    # Numbering counts only shown calls: a dropped call is always the subagent's last one,
    # so no gap gives it away (the journal `seq` would, and would count the orchestrator's calls).
    for step, r in enumerate(shown, start=1):
        if r.tool not in ("create_charge", *READ_TOOLS):
            continue
        charges += r.tool == "create_charge"
        lines.append(f"- step {step}: {r.tool} {_invoice(r)}{_key(r, show_key)}: {_outcome(r)}")
    if charges == 0:
        lines.append("- no create_charge calls")
    if hedge and any(r.tool == "create_charge" and r.outcome == "timeout" for r in shown):
        lines.append(TIMEOUT_NOTE)
    return "\n".join(lines)


def _reached_bank(r: CallRow) -> bool:
    return r.outcome not in ("dropped", "rejected")


def _key(r: CallRow, show: bool) -> str:
    # code_key: the key the subagent sent, so the orchestrator can pass it on (M4).
    value = r.args.get("idempotency_key")
    if not show or r.tool != "create_charge":
        return ""
    if not isinstance(value, str) or not value:
        return " (no idempotency key)"
    return f" (idempotency key {value if _KEY.fullmatch(value) else '?'})"


def _invoice(r: CallRow) -> str:
    # The subagent writes the arguments: only an id-like value goes into a code-built line.
    value = r.args.get("invoice_id")
    return value if isinstance(value, str) and _INVOICE_ID.fullmatch(value) else "?"


def _outcome(r: CallRow) -> str:
    if r.outcome == "timeout":
        return "timeout, outcome unknown"
    if r.outcome != "ok":
        return f"error {r.error_code}"
    if is_processing(r):
        return f"processing, final outcome not known yet, charge {r.result['charge_id']}"
    if capture_status(r) == "declined":
        # The statement reads the capture, as PayPal asks: the order's `completed` is not money.
        return f"declined, charge {r.result['charge_id']}"
    if r.tool == "create_charge":
        return f"success, charge {r.result['charge_id']}"
    if r.tool == "get_invoice":
        return f"status {r.result['status']}"
    ids = [_listed(c) for c in r.result]
    if not ids:
        return "no charges"
    return f"{len(ids)} charge{'s' if len(ids) > 1 else ''} ({', '.join(ids)})"


def _listed(charge: dict) -> str:
    status = charge.get("status", "succeeded")
    if status == "completed":
        status = charge["captures"][0]["status"]
        status = "succeeded" if status == "completed" else status
    return charge["charge_id"] if status == "succeeded" else f"{charge['charge_id']} {status}"


RENDERERS: dict[Channel, Renderer] = {
    "text": lambda child, calls, report_lost: "" if report_lost else subagent_words(child),
    # Like the words, the form is the subagent's own report: a lost report loses it too.
    "struct": lambda child, calls, report_lost: "" if report_lost else form(child),
    "code": lambda child, calls, report_lost: statement(calls),
    "code_key": lambda child, calls, report_lost: statement(calls, show_key=True),
    "code_hedged": lambda child, calls, report_lost: statement(calls, hedge=True),
    # The run, not the report: a lost report would still show in it, so nothing is passed on.
    "retell": lambda child, calls, report_lost: "" if report_lost else transcript(child),
    # A single agent never delegates; the entry only keeps the episode wiring uniform.
    "none": lambda child, calls, report_lost: subagent_words(child),
}
