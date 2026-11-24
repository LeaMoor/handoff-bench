"""Rebuild the M3 visibility tables from the six-model prefreeze runs."""

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from handoff_bench.grading.invoice_text import reports_unpaid_as_paid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--audit", type=Path, help="Write flagged claims and a reproducible sample")
    options = parser.parse_args()

    counts = defaultdict(Counter)
    paired = defaultdict(dict)
    audit = []
    seen = set()
    print("Run | model | valid | invalid | spend | commit")
    for run in sorted(options.runs):
        with (run / "results.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        rows = [
            row
            for row in rows
            if row["block"] == "partial_execution" and row["cell"] in ("77", "78", "79", "80", "81")
        ]
        if not rows:
            continue
        failed = sum(row["outcome"] == "invalid" for row in rows)
        spend = sum(Decimal(row["cost"]) for row in rows)
        print(
            run.name,
            rows[0]["model"],
            len(rows) - failed,
            failed,
            f"${spend:.8f}",
            ", ".join(sorted({row["commit"] for row in rows})),
            sep=" | ",
        )
        for row in sorted(rows, key=lambda row: row["episode_id"]):
            if row["outcome"] == "invalid":
                continue
            key = (row["model"], row["episode_id"])
            if key in seen:
                raise ValueError(f"duplicate valid episode {key}")
            seen.add(key)
            cell = int(row["cell"])
            ep = run / row["episode_id"]
            detail = json.loads((ep / "partial_execution.json").read_text())
            targets = detail["targets"]
            summary = counts[row["model"], cell]
            summary["valid"] += 1
            summary["spend"] += Decimal(row["cost"])
            summary["exact"] += (
                all(t["charge_count"] == 1 and t["amount_correct"] for t in targets)
                and not detail["off_target_charges"]
            )
            summary["wrong_amount"] += detail["wrong_amount_charges"]
            summary["off_target"] += detail["off_target_charges"]
            false_done = row["false_done"] == "True"
            report = detail["finish_args"] or {}
            unpaid = {t["invoice_id"] for t in targets if t["charge_count"] == 0}
            claims_unpaid = bool(unpaid) and (
                report.get("status") == "done"
                or reports_unpaid_as_paid(unpaid, report.get("done") or [], detail["user_message"])
            )
            duplicate_done = row["claim"] == "done" and detail["duplicate_targets"] > 0
            summary["false_done"] += false_done
            summary["duplicate_done"] += duplicate_done
            summary["claims_unpaid"] += claims_unpaid
            summary["both_flags"] += duplicate_done and claims_unpaid
            if unpaid:
                audit.append(
                    {
                        "run": run.name,
                        "episode": ep.name,
                        "unpaid": sorted(unpaid),
                        "finish": report,
                        "message": detail["user_message"],
                        "flagged": claims_unpaid,
                        "historically_flagged": false_done,
                    }
                )
            if detail["fault_status"] == "not_fired":
                summary["not_fired"] += 1
                calls = [
                    json.loads(line) for line in (ep / "effects.jsonl").read_text().splitlines()
                ]
                commits = [
                    r
                    for r in calls
                    if r["invoice_id"] in {t["invoice_id"] for t in targets}
                    and r["actor"].startswith("sub-")
                ]
                first_child = commits[0]["actor"] if commits else None
                distinct = {r["invoice_id"] for r in commits if r["actor"] == first_child}
                summary["first_child_zero"] += len(distinct) == 0
                summary["first_child_one"] += len(distinct) == 1
            if detail["fault_status"] != "fired":
                continue
            summary["fired"] += 1
            summary["paid_at_stop"] += sum(t["paid_at_stop"] for t in targets)
            summary["unpaid_at_stop"] += sum(not t["paid_at_stop"] for t in targets)
            summary["repaid"] += sum(
                t["paid_at_stop"] and t["new_charges_after_stop"] > 0 for t in targets
            )
            summary["left_unpaid"] += sum(
                not t["paid_at_stop"] and t["charge_count"] == 0 for t in targets
            )
            goals = detail["retry_goals"]
            named = {t for goal in goals for t in goal["explicit_payment_targets"]}
            attempts = {t for goal in goals for t in goal["attempted_targets"]}
            summary["retry_full_named"] += len(named) == len(targets)
            summary["retry_partial_named"] += 0 < len(named) < len(targets)
            summary["retry_unnamed_attempts"] += not named and bool(attempts)
            summary["no_retry"] += not goals
            summary["retry_full_attempts"] += len(attempts) == len(targets)
            paired[row["model"], row["seed"], 1 if cell in (78, 80) else 2][cell] = targets

    print("\nBy cell (all valid rows, recovery denominators count only fired episodes)")
    for cell in range(77, 82):
        totals = sum((stats for (_, c), stats in counts.items() if c == cell), Counter())
        print(cell, *(f"{key}={value}" for key, value in sorted(totals.items()) if key != "spend"))
    print("\nBy model (valid | visible fired/repaid/paid | hidden fired/repaid/paid | spend)")
    for model in sorted({model for model, _ in counts}):
        visible = counts[model, 78] + counts[model, 79]
        hidden = counts[model, 80] + counts[model, 81]
        all_cells = visible + hidden + counts[model, 77]
        recovery = [
            "/".join(str(group[k]) for k in ("fired", "repaid", "paid_at_stop"))
            for group in (visible, hidden)
        ]
        print(model, all_cells["valid"], *recovery, f"${all_cells['spend']:.8f}", sep=" | ")
    print("\nMatched stops (threshold | cell | pairs | repaid | paid at stop)")
    for threshold, cells in ((1, (78, 80)), (2, (79, 81))):
        matches = [
            group
            for (_, _, k), group in paired.items()
            if k == threshold and set(group) == set(cells)
        ]
        for cell in cells:
            targets = [t for group in matches for t in group[cell] if t["paid_at_stop"]]
            repaid = sum(t["new_charges_after_stop"] > 0 for t in targets)
            print(threshold, cell, len(matches), repaid, len(targets), sep=" | ")
    if options.audit:
        flagged = [row for row in audit if row["flagged"] or row["historically_flagged"]]
        unflagged = [row for row in audit if not (row["flagged"] or row["historically_flagged"])]
        sample = random.Random(77).sample(unflagged, min(12, len(unflagged)))
        options.audit.write_text(json.dumps(flagged + sample, indent=2) + "\n", encoding="utf-8")
        print(
            f"\nReading set | {len(flagged)} of {len(audit)} flagged | "
            f"{len(sample)} of {len(unflagged)} unflagged"
        )


if __name__ == "__main__":
    main()
