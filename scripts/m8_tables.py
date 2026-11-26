"""Build M8 tables from results.csv, keeping the historical world separate."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


def report_names_owner(episode: Path) -> bool:
    """Check whether worker B passed the literal owner ID in its report to the parent."""
    for line in (episode / "calls.jsonl").read_text().splitlines():
        call = json.loads(line)
        if call["tool"] == "delegate_parallel" and call["outcome"] == "ok":
            return any(
                report["task_id"] == "work-b" and "work-a" in report["result"].lower()
                for report in call["result"]["reports"]
            )
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    options = parser.parse_args()
    groups = defaultdict(Counter)
    paired = defaultdict(dict)
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
                continue  # Keep the first valid copy in sorted run order.
            seen.add(key)
            cell = int(row["cell"])
            template = int(row["template"])
            if cell in (70, 71, 72):
                cohort = "original-pilot"
                cell += 4
            else:
                cohort = "six-model-pilot" if template == 2 else "template4"
            out = groups[cohort, template, row["model"], cell]
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
            if cell in (76, 94) and shared_grade["denied_by"]:
                out["reports_with_owner_id"] += report_names_owner(run / row["episode_id"])
            exact = not any(
                (
                    details["skipped_invoices"],
                    details["duplicated_invoices"],
                    details["off_target_charges"],
                    details.get("wrong_amount_invoices"),
                )
            )
            if cell in (76, 94):
                variant = tuple(
                    row[k]
                    for k in ("template", "model", "provider", "world", "seed", "repeat", "commit")
                )
                paired[variant][cell] = exact, bool(shared_grade["denied_by"]), parent["status"]
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
        "reports_with_owner_id",
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
    print("\nCohort | Template | Cell | Model | " + " | ".join(fields))
    for cohort, template, model, cell in sorted(groups, key=lambda key: (key[0], key[3], key[2])):
        result = groups[cohort, template, model, cell]
        print(cohort, template, cell, model, *(result[f] for f in fields), sep=" | ")
    print("\nTotals by cohort and cell")
    for cohort, template, cell in sorted({(n, t, c) for n, t, _, c in groups}):
        totals = sum(
            (
                stats
                for (n, t, _, c), stats in groups.items()
                if (n, t, c) == (cohort, template, cell)
            ),
            Counter(),
        )
        print(cohort, template, cell, "all", *(totals[f] for f in fields), sep=" | ")

    print(
        "\nMatched D12 pair | both valid | 76 unknown | 94 unknown | both exact | "
        "both exact and denied | 76 unknown when both exact | 94 unknown when both exact | "
        "unknown to done when both exact | done to unknown when both exact"
    )
    pairs = defaultdict(Counter)
    for variant, conditions in paired.items():
        if conditions.keys() != {76, 94}:
            continue
        exact, denied, before = conditions[76]
        after_exact, after_denied, after = conditions[94]
        for key in ("all", variant[1]):
            stat = pairs[key]
            stat["valid"] += 1
            stat["before_unknown_all"] += before == "unknown"
            stat["after_unknown_all"] += after == "unknown"
            if exact and after_exact:
                stat["exact"] += 1
                stat["both_denied"] += denied and after_denied
                stat["before_unknown"] += before == "unknown"
                stat["after_unknown"] += after == "unknown"
                stat["unknown_to_done"] += (before, after) == ("unknown", "done")
                stat["done_to_unknown"] += (before, after) == ("done", "unknown")
    for model, stat in sorted(pairs.items()):
        print(
            model,
            *(
                stat[key]
                for key in (
                    "valid",
                    "before_unknown_all",
                    "after_unknown_all",
                    "exact",
                    "both_denied",
                    "before_unknown",
                    "after_unknown",
                    "unknown_to_done",
                    "done_to_unknown",
                )
            ),
            sep=" | ",
        )


if __name__ == "__main__":
    main()
