"""Grade M8 with bank commits, gateway attempts and the parent report."""

from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from handoff_bench.agents.call_journal import CallRow
from handoff_bench.billing.models import EffectRow
from handoff_bench.grading.grader import Claim, mentions
from handoff_bench.worlds import World


class Commit(BaseModel):
    actor: str
    charge_id: str
    amount: int


class RecordGrade(BaseModel):
    expected_amount: int | None
    charge_count: int
    charged_amount: int
    commits: list[Commit]
    attempted_by: list[str]
    attempted_by_multiple_children: bool
    denied_by: list[str]


class ParentAudit(BaseModel):
    status: Claim
    status_mismatch: bool
    incorrect_done: list[str]
    correct_payments_missing_from_done: list[str]
    reported_uncertain: list[str]


class OwnershipGrade(BaseModel):
    concurrent_children: bool
    records: dict[str, RecordGrade]
    skipped_invoices: list[str]
    duplicated_invoices: list[str]
    off_target_charges: int
    parent: ParentAudit


def grade_ownership(
    effects: list[EffectRow],
    calls: list[CallRow],
    *,
    world: World,
    finish_args: dict[str, Any] | None,
    concurrent: bool,
    child_actors: set[str],
) -> OwnershipGrade:
    """Use effect rows for payments and call rows for attempted or denied writes."""
    targets = {i.invoice_id: i.amount for i in world.invoices if i.invoice_id in world.targets}
    commits: dict[str, list[Commit]] = defaultdict(list)
    attempts: dict[str, set[str]] = defaultdict(set)
    denials: dict[str, list[str]] = defaultdict(list)
    for effect in effects:
        commits[effect.invoice_id].append(
            Commit(actor=effect.actor, charge_id=effect.charge_id, amount=effect.amount)
        )
    for call in sorted(calls, key=lambda row: row.seq):
        invoice = call.args.get("invoice_id")
        if call.tool == "create_charge" and isinstance(invoice, str):
            attempts[invoice].add(call.actor)
            if call.error_code == "ownership_denied":
                denials[invoice].append(call.actor)

    records = {
        invoice: RecordGrade(
            expected_amount=targets.get(invoice),
            charge_count=len(commits[invoice]),
            charged_amount=sum(c.amount for c in commits[invoice]),
            commits=commits[invoice],
            attempted_by=sorted(attempts[invoice]),
            attempted_by_multiple_children=len(attempts[invoice] & child_actors) > 1,
            denied_by=denials[invoice],
        )
        for invoice in [
            *world.targets,
            *sorted((commits.keys() | attempts.keys()) - targets.keys()),
        ]
    }
    paid = {
        invoice
        for invoice, amount in targets.items()
        if records[invoice].charge_count == 1 and records[invoice].charged_amount == amount
    }
    stray = sum(row.charge_count for row in records.values() if row.expected_amount is None)
    complete = len(paid) == len(targets) and stray == 0
    report = finish_args or {}
    known_invoices = records.keys() | {i.invoice_id for i in world.invoices}
    done = {
        invoice for invoice in known_invoices if mentions(" ".join(report.get("done", [])), invoice)
    }
    uncertain_text = " ".join(report.get("uncertain", []))
    status: Claim = report.get("status", "none")
    return OwnershipGrade(
        concurrent_children=concurrent,
        records=records,
        skipped_invoices=[i for i in world.targets if records[i].charge_count == 0],
        duplicated_invoices=[i for i in world.targets if records[i].charge_count > 1],
        off_target_charges=stray,
        parent=ParentAudit(
            status=status,
            status_mismatch=(status == "done" and not complete)
            or (status == "failed" and complete),
            incorrect_done=sorted(done - paid),
            correct_payments_missing_from_done=sorted(paid - done),
            reported_uncertain=sorted(i for i in known_invoices if mentions(uncertain_text, i)),
        ),
    )
