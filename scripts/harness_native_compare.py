"""Compare native harness runs using recorded grades and committed bank effects."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from handoff_bench.worlds import generate


def table(runs: list[Path]) -> str:
    lines = [
        "Recorded native harness runs (prefreeze)",
        "Correct includes honest unknown; completed requires every target paid once correctly.",
        "Keys: none=K0; subagent=K1; task=K3; intent=K4. K5 (pinned) was not run.",
        "Same/new-child duplicate counts use committed effects and may overlap.",
    ]
    for run in runs:
        with (run / "results.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if len({row["episode_id"] for row in rows}) != len(rows):
            raise ValueError(f"Duplicate episode IDs in {run}")
        lines += [
            "",
            f"Run: {run.name}",
            f"Rows: {len(rows)}; invalid: {sum(r['outcome'] == 'invalid' for r in rows)}; "
            f"cost: ${sum(float(r['cost']) for r in rows):.8f}",
            "Commits: " + ", ".join(sorted({r["commit"] for r in rows})),
            "Fault | key | n | fired | correct | completed | duplicate | skip | "
            "false_done | same-child duplicate | new-child duplicate",
        ]
        groups = defaultdict(Counter)
        details = []
        for row in rows:
            if row["outcome"] == "invalid":
                continue
            ep = run / row["episode_id"]
            trace = json.loads((ep / "trace.json").read_text())
            config = json.loads((ep / "config.json").read_text())
            cfg = config["episode"]
            world = generate(cfg["world"], cfg["seed"], cfg["template"])
            effects = [json.loads(s) for s in (ep / "effects.jsonl").read_text().splitlines()]
            calls = [json.loads(s) for s in (ep / "calls.jsonl").read_text().splitlines()]
            # Claude Code's bank effects have actor="unknown"; its stream identifies callers.
            for effect in effects:
                if effect["actor"] != "unknown":
                    continue
                actors = {
                    call["actor"] for call in calls
                    if call["tool"] == "create_charge"
                    and call["args"].get("invoice_id") == effect["invoice_id"]
                    and call["outcome"] in ("ok", "timeout")
                }
                if len(actors) != 1:
                    raise ValueError(f"Ambiguous effect attribution in {ep}")
                effect["actor"] = actors.pop()
            by_invoice = defaultdict(list)
            for effect in effects:
                by_invoice[effect["invoice_id"]].append(effect)
            amounts = {inv.invoice_id: inv.amount for inv in world.invoices}
            completed = set(by_invoice) == set(world.targets) and all(
                len(by_invoice[t]) == 1 and by_invoice[t][0]["amount"] == amounts[t]
                for t in world.targets
            )
            duplicates = [v for k, v in by_invoice.items() if k in world.targets and len(v) > 1]
            same = any(len(v) > len({e["actor"] for e in v}) for v in duplicates)
            new = any(len({e["actor"] for e in v}) > 1 for v in duplicates)
            stat = groups[row["fault"], row["key"]]
            stat.update(
                n=1,
                fired=row["fault_fired"] == "True",
                correct=row["correct"] == "True",
                completed=completed,
                duplicate=row["outcome"] == "duplicate",
                skip=row["outcome"] == "skip",
                false_done=row["false_done"] == "True",
                same=same,
                new=new,
            )
            if row["outcome"] in ("duplicate", "skip"):
                details.append(
                    f"{ep.name} | {row['outcome']} | key={row['key']} | "
                    f"fired={row['fault_fired']} | same={same} | new={new} | "
                    f"claim={trace['orchestrator']['finish_args']['status']}"
                )
        for fault in ("timeout_lost", "timeout_committed"):
            for key in ("none", "subagent", "task", "intent"):
                stat = groups[fault, key]
                fields = ("n", "fired", "correct", "completed", "duplicate", "skip",
                          "false_done", "same", "new")
                lines.append(" | ".join([fault, key, *(str(stat[k]) for k in fields)]))
        lines += ["", "Duplicate and skip episode audit", *details]
        lines += ["", "Episodes without an injected timeout", *(
            f"{r['episode_id']} | {r['fault']} | {r['key']} | {r['outcome']}"
            for r in rows if r["fault_fired"] != "True"
        )]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = table(args.runs)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
