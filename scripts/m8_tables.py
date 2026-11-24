"""Build M8 tables from results.csv, keeping the historical world separate."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    options = parser.parse_args()
    groups = defaultdict(Counter)
    seen = set()

    print("Run | model | provider | rows | invalid | spend | commit")
    for run in sorted(options.runs):
        with (run / "results.csv").open(newline="", encoding="utf-8") as stream:
            rows = [row for row in csv.DictReader(stream) if row["block"] == "ownership"]
        if not rows:
            continue
        spend = sum(Decimal(row["cost"]) for row in rows)
        print(
            run.name,
            rows[0]["model"],
            rows[0]["provider"],
            len(rows),
            sum(row["outcome"] == "invalid" for row in rows),
            f"${spend:.8f}",
            ",".join(sorted({row["commit"] for row in rows})),
            sep=" | ",
        )
        for row in rows:
            if row["outcome"] == "invalid":
                continue
            key = row["model"], row["episode_id"]
            if key in seen:
                raise ValueError(f"duplicate valid episode {key}")
            seen.add(key)
            cell = int(row["cell"])
            cell = {70: 74, 71: 75, 72: 76}.get(cell, cell)
            out = groups[int(row["template"]), row["model"], cell]
            details = json.loads(row["ownership"])
            config = json.loads((run / row["episode_id"] / "config.json").read_text())
            shared = config["worklists"]["work-a"][1]
            shared_grade = details["records"][shared]
            parent = details["parent"]
            out["valid"] += 1
            out["concurrent"] += details["concurrent_children"]
            out["both_attempted"] += shared_grade["attempted_by_multiple_children"]
            out["shared_duplicate"] += shared_grade["charge_count"] > 1
            out["denied"] += len(shared_grade["denied_by"])
            out["episodes_with_denial"] += bool(shared_grade["denied_by"])
            exact = not any(
                (
                    details["skipped_invoices"],
                    details["duplicated_invoices"],
                    details["off_target_charges"],
                    details.get("wrong_amount_invoices"),
                )
            )
            out["all_exact"] += exact
            out["all_exact_unknown"] += exact and parent["status"] == "unknown"
            out["denied_all_exact"] += exact and bool(shared_grade["denied_by"])
            out["denied_all_exact_unknown"] += (
                exact and bool(shared_grade["denied_by"]) and parent["status"] == "unknown"
            )
            out["skipped"] += len(details["skipped_invoices"])
            out["wrong_amount"] += len(details.get("wrong_amount_invoices", []))
            out["off_target"] += details["off_target_charges"]
            out["message_conflict"] += bool(parent.get("message_conflicts"))
            out[f"parent_{parent['status']}"] += 1
            out["shared_uncertain"] += shared in parent["reported_uncertain"]

    fields = (
        "valid",
        "concurrent",
        "both_attempted",
        "shared_duplicate",
        "denied",
        "episodes_with_denial",
        "all_exact",
        "all_exact_unknown",
        "denied_all_exact",
        "denied_all_exact_unknown",
        "skipped",
        "wrong_amount",
        "off_target",
        "message_conflict",
        "parent_done",
        "parent_unknown",
        "parent_failed",
        "parent_none",
        "shared_uncertain",
    )
    print("\nTemplate | cell | model | " + " | ".join(fields))
    for template, model, cell in sorted(groups, key=lambda key: (key[0], key[2], key[1])):
        print(template, cell, model, *(groups[template, model, cell][f] for f in fields), sep=" | ")
    print("\nTotals by template and cell")
    for template, cell in sorted({(t, c) for t, _, c in groups}):
        totals = sum(
            (n for (t, _, c), n in groups.items() if (template, cell) == (t, c)), Counter()
        )
        print(template, cell, "all", *(totals[f] for f in fields), sep=" | ")


if __name__ == "__main__":
    main()
