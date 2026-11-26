"""Run from the repo: uv run python docs/results/2026-11-26/поведение.py"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from handoff_bench.worlds import generate

HERE = Path(__file__).resolve().parent
RUNS = HERE.parents[2] / "runs"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def export(name, groups):
    rows = [
        dict(zip(("run", "condition"), group, strict=True), **counts)
        for group, counts in groups.items()
    ]
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (HERE / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, restval=0)
        writer.writeheader()
        writer.writerows(rows)


def main():
    groups = defaultdict(Counter)
    pilots = defaultdict(Counter)
    for run in ["prefreeze-04-main-haiku", "prefreeze-05-main-luna"]:
        with (RUNS / run / "results.csv").open() as stream:
            for row in csv.DictReader(stream):
                counts = pilots[run, row["channel"]]
                counts.update(
                    n=1,
                    correct=int(row["correct"] == "True"),
                    checked=int(row["self_checked"] == "True"),
                    said_unknown=int(row["claim"] == "unknown"),
                )
    export("проверки_первый_прогон.csv", pilots)
    for harness in ("cc", "hermes", "codex"):
        run = f"harness-{harness}-native"
        with (RUNS / run / "results.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        assert len({r["episode_id"] for r in rows}) == len(rows) == 168
        for r in rows:
            assert r["outcome"] != "invalid"
            ep = RUNS / run / r["episode_id"]
            world = generate(int(r["world"]), int(r["seed"]), int(r["template"]))
            amounts = {i.invoice_id: i.amount for i in world.invoices}
            effects = read_jsonl(ep / "effects.jsonl")
            charges = Counter(e["invoice_id"] for e in effects)
            completed = charges == Counter(world.targets) and all(
                e["amount"] == amounts[e["invoice_id"]] for e in effects
            )
            calls = read_jsonl(ep / "calls.jsonl")
            focal = [
                c
                for c in calls
                if c["tool"] == "create_charge"
                and c["args"].get("invoice_id") == world.focal_invoice
            ]
            payers = Counter(c["actor"] for c in focal)
            counts = groups[run, r["fault"]]
            counts.update(
                n=1,
                completed=int(completed),
                any_unpaid=int(any(not charges[t] for t in world.targets)),
                reported_done=int(r["claim"] == "done"),
                reported_unknown=int(r["claim"] == "unknown"),
                reported_failed=int(r["claim"] == "failed"),
                same_actor_retried=int(any(n > 1 for n in payers.values())),
                multiple_actors_attempted=int(len(payers) > 1),
                done_incomplete=int(r["claim"] == "done" and not completed),
            )
    export("исходы_и_ответы_native.csv", groups)


if __name__ == "__main__":
    main()
