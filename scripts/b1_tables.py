"""Summarize B1 and same-snapshot controls from episode CSVs and traces."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median

CELLS = {
    24: ("delegated", "lost"),
    25: ("delegated", "committed"),
    30: ("B0", "lost"),
    31: ("B0", "committed"),
    95: ("B1-clean", "lost"),
    96: ("B1-clean", "committed"),
    97: ("B1-dirty", "lost"),
    98: ("B1-dirty", "committed"),
}


def review_metrics(episode: Path) -> Counter[str]:
    """Count distinct turns with a review call before a subsequent payment or finish."""
    parent = json.loads((episode / "trace.json").read_text())["orchestrator"]
    messages = parent["messages"]
    call_rows = [json.loads(line) for line in (episode / "calls.jsonl").read_text().splitlines()]
    rows = sorted(call_rows, key=lambda row: row["seq"])
    anchor = next(row["seq"] for row in rows if row["outcome"] == "timeout")
    timed_out_invoice = next(row["args"]["invoice_id"] for row in rows if row["seq"] == anchor)
    counts = Counter()
    offset = 0
    for message in messages:
        size = len(message.get("tool_calls") or [])
        batch = rows[offset : offset + size]
        offset += size
        if not batch or batch[0]["seq"] <= anchor:
            continue
        if any(
            row["tool"] == "finish"
            or (
                row["tool"] == "create_charge"
                and row["args"].get("invoice_id") == timed_out_invoice
            )
            for row in batch
        ):
            break
        calls = [row for row in batch if row["tool"] == "review_workflow"]
        counts["review_turns"] += bool(calls)
        counts["batched"] += len(calls) > 1
        counts["calls"] += len(calls)
        counts["errors"] += sum(row["error_code"] == "review_unavailable" for row in calls)
    return counts


def model_calls(episode: Path) -> int:
    """Count all model calls, including child calls in delegated controls."""
    with (episode / "model_calls.jsonl").open(encoding="utf-8") as stream:
        return sum(1 for _ in stream)


def table(series: list[tuple[dict[str, str], Path]]) -> None:
    groups = defaultdict(list)
    for row, path in series:
        groups[row["model"], int(row["cell"])].append((row, path))
        groups["all", int(row["cell"])].append((row, path))
    print(
        "Model | Cell | Cohort | World | Valid | Fired | Duplicate | Unpaid and done | "
        "Honest unknown | No finish | No charge | Median parent turns | Median model calls | "
        "Median prompt tokens | "
        "2+ review turns | Batched reviews | 3+ review calls | Review errors"
    )
    for (model, cell), group in sorted(groups.items()):
        fired = [(row, ep) for row, ep in group if row["fault_fired"] == "True"]
        count = Counter()
        for row, ep in fired:
            count["duplicate"] += row["effect"] == "duplicate"
            count["unpaid_done"] += row["effect"] == "skip" and row["claim"] == "done"
            count["unknown"] += row["honest_unknown"] == "True"
            count["no_finish"] += row["claim"] == "none"
            count["unpaid"] += row["effect"] == "skip"
            if cell >= 95:
                metrics = review_metrics(ep)
                count["separate"] += metrics["review_turns"] >= 2
                count["batched"] += metrics["batched"] > 0
                count["extra"] += metrics["calls"] > 2
                count["errors"] += metrics["errors"]
        fields = ("duplicate", "unpaid_done", "unknown", "no_finish", "unpaid")
        print(
            model,
            cell,
            *CELLS[cell],
            len(group),
            len(fired),
            *(count[key] for key in fields),
            median(int(row["steps"]) for row, _ in fired) if fired else "-",
            median(model_calls(ep) for _, ep in fired) if fired else "-",
            median(int(row["prompt_tokens"]) for row, _ in fired) if fired else "-",
            *(
                count[key] if cell >= 95 else "-"
                for key in ("separate", "batched", "extra", "errors")
            ),
            sep=" | ",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    options = parser.parse_args()
    variants = defaultdict(dict)
    print("Run | Model | Provider | Rows | Invalid | Spend | Commit")
    for run in sorted(options.runs):
        with (run / "results.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            continue
        print(
            run.name,
            rows[0]["model"],
            rows[0]["provider"],
            len(rows),
            sum(row["outcome"] == "invalid" for row in rows),
            f"${sum(Decimal(row['cost']) for row in rows):.8f}",
            ",".join(sorted({row["commit"] for row in rows})),
            sep=" | ",
        )
        for row in rows:
            cell = int(row["cell"])
            if cell in CELLS and row["outcome"] != "invalid":
                key = tuple(
                    row[k]
                    for k in (
                        "model",
                        "provider",
                        "served_model",
                        "served_provider",
                        "world",
                        "template",
                        "seed",
                        "repeat",
                        "commit",
                    )
                )
                variants[key].setdefault(cell, (row, run / row["episode_id"]))

    print("\nAll valid episodes; outcomes count only episodes where the fault fired")
    table([entry for conditions in variants.values() for entry in conditions.values()])
    paired = [conditions for conditions in variants.values() if conditions.keys() == CELLS.keys()]
    print(
        f"\nMatched across all eight cells | {len(paired)} model-provider-world-template-seed pairs"
    )
    table([entry for conditions in paired for entry in conditions.values()])


if __name__ == "__main__":
    main()
