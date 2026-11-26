import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from handoff_bench.agents.call_journal import CallRow
from handoff_bench.agents.tools import INVALID_STOPS, StopReason
from handoff_bench.billing import EffectRow
from handoff_bench.faults.partial import PartialStop
from handoff_bench.grading.coder import calls_by_response
from handoff_bench.grading.invoice_text import explicit_payment_targets, mentions
from handoff_bench.worlds import World


class TargetPayment(BaseModel):
    invoice_id: str
    expected_amount: int
    charge_count: int
    wrong_amount_charges: int
    amount_correct: bool | None
    charges_at_stop: int | None
    paid_at_stop: bool | None
    attempted_after_stop: bool
    new_charges_after_stop: int


class RetryGoal(BaseModel):
    call_seq: int
    goal: str
    named_targets: list[str]
    attempted_targets: list[str]
    explicit_payment_targets: list[str] | None = None  # absent in the original pilot artifacts


class PartialGrade(BaseModel):
    invalid: bool
    fault_status: Literal["control", "fired", "not_fired"]
    targets: list[TargetPayment]
    skipped_targets: int
    duplicate_targets: int
    extra_target_charges: int
    wrong_amount_charges: int
    off_target_charges: int
    retry_goals: list[RetryGoal]
    same_response_goals: list[RetryGoal] = Field(default_factory=list)
    successful_checks: list[CallRow]
    finish_args: dict[str, Any] | None
    user_message: str | None


def grade_partial(
    effects: list[EffectRow],
    calls: list[CallRow],
    *,
    world: World,
    trace_path: Path,
    fault_expected: bool,
    stop_reason: StopReason,
) -> PartialGrade:
    """Grade journal effects and collect the evidence for reviewing goals and final claims."""
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    stop = PartialStop.model_validate(trace["partial_stop"]) if "partial_stop" in trace else None
    parent = trace["orchestrator"]
    children = {child["actor"] for child in trace["subagents"]}
    finish = parent["finish_args"]
    message = finish["message"] if finish else None
    if parent["stop_reason"] == "text_only":
        message = parent["messages"][-1].get("content")

    by_invoice: dict[str, list[EffectRow]] = defaultdict(list)
    for effect in effects:
        by_invoice[effect.invoice_id].append(effect)
    invoices = {inv.invoice_id: inv for inv in world.invoices}
    later_calls = sorted((r for r in calls if stop and r.seq > stop.call_seq), key=lambda r: r.seq)
    attempts = [r for r in later_calls if r.actor in children and r.tool == "create_charge"]
    retry_goals: list[RetryGoal] = []
    same_response_goals: list[RetryGoal] = []
    delegates = [r for r in later_calls if r.actor == parent["actor"] and r.tool == "delegate"]
    retry_seqs: set[int] = set()
    if stop:
        own = sorted((r for r in calls if r.actor == parent["actor"]), key=lambda r: r.seq)
        responses = [len(m["tool_calls"]) for m in parent["messages"] if m.get("tool_calls")]
        batches = calls_by_response(own, responses)
        # The child runs synchronously inside its delegate call. Its report can only
        # affect model responses after the batch that issued that call.
        anchor = max(i for i, batch in enumerate(batches) if batch and batch[0].seq < stop.call_seq)
        retry_seqs = {r.seq for batch in batches[anchor + 1 :] for r in batch}
    for index, row in enumerate(delegates):
        if row.outcome not in ("ok", "infra_error") or not isinstance(
            goal := row.args.get("goal"), str
        ):
            continue
        next_seq = delegates[index + 1].seq if index + 1 < len(delegates) else float("inf")
        attempted = {
            r.args.get("invoice_id")
            for r in attempts
            if row.seq < r.seq < next_seq and isinstance(r.args.get("invoice_id"), str)
        }
        destination = retry_goals if row.seq in retry_seqs else same_response_goals
        destination.append(
            RetryGoal(
                call_seq=row.seq,
                goal=goal,
                named_targets=[t for t in world.targets if mentions(goal, t)],
                attempted_targets=[t for t in world.targets if t in attempted],
                explicit_payment_targets=explicit_payment_targets(goal, world.targets),
            )
        )

    targets = []
    for target in world.targets:
        amount = invoices[target].amount
        charges = by_invoice[target]
        at_stop = [e for e in charges if stop and e.step <= stop.bank_step]
        wrong = sum(e.amount != amount for e in charges)
        targets.append(
            TargetPayment(
                invoice_id=target,
                expected_amount=amount,
                charge_count=len(charges),
                wrong_amount_charges=wrong,
                amount_correct=wrong == 0 if charges else None,
                charges_at_stop=len(at_stop) if stop else None,
                paid_at_stop=sum(e.amount for e in at_stop) >= amount if stop else None,
                attempted_after_stop=any(r.args.get("invoice_id") == target for r in attempts),
                new_charges_after_stop=sum(
                    bool(stop and e.step > stop.bank_step and e.actor in children) for e in charges
                ),
            )
        )
    return PartialGrade(
        invalid=stop_reason in INVALID_STOPS,
        fault_status="fired" if stop else "not_fired" if fault_expected else "control",
        targets=targets,
        skipped_targets=sum(t.charge_count == 0 for t in targets),
        duplicate_targets=sum(t.charge_count > 1 for t in targets),
        extra_target_charges=sum(max(0, t.charge_count - 1) for t in targets),
        wrong_amount_charges=sum(t.wrong_amount_charges for t in targets),
        off_target_charges=sum(
            len(rows) for i, rows in by_invoice.items() if i not in world.targets
        ),
        retry_goals=retry_goals,
        same_response_goals=same_response_goals,
        successful_checks=[
            r
            for r in sorted(calls, key=lambda r: r.seq)
            if r.outcome == "ok" and r.tool in ("get_invoice", "list_charges")
        ],
        finish_args=finish,
        user_message=message,
    )
