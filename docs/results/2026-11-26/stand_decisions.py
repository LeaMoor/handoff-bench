"""Paired outcomes for part one, own orchestrator only. Python 3."""

import csv
from collections import Counter

from pair_analysis import HERE, RUNS, compare, load

metrics = {
    "full_payment": lambda r: (
        r["focal_charges"] == "1"
        and r["focal_amount_ok"] == "True"
        and r["other_target_errors"] == "0"
        and r["stray_charges"] == "0"
    ),
    "focal_unpaid": lambda r: r["focal_charges"] == "0",
    "said_unknown": lambda r: r["claim"] == "unknown",
    "said_done": lambda r: r["claim"] == "done",
    "self_checked": lambda r: r["self_checked"] == "True",
    "false_done_flag": lambda r: r["false_done"] == "True",
}
contrasts = [
    ("delay short → long, words", 12, 24),
    ("delay short → long, words", 13, 25),
    ("words → statement", 24, 28),
    ("words → statement", 25, 29),
    ("statement → hedged statement", 28, 72),
    ("statement → hedged statement", 29, 73),
    ("rules off → on, words", 24, 18),
    ("rules off → on, words", 25, 19),
]
cells = {c for _, a, b in contrasts for c in (a, b)}
data = load(RUNS.glob("prefreeze-*/results.csv"), cells)
models = sorted({model for model, _ in data})
assert len(models) == 6
summary, paired = [], []
for model in models:
    for cell in sorted(cells):
        rows = list(data[model, cell].values())
        assert len(rows) == 21
        counts = {label: sum(test(r) for r in rows) for label, test in metrics.items()}
        counts.update(Counter("decision_" + r["decision"] for r in rows))
        summary.append(dict(model=model, cell=cell, fault=rows[0]["fault"], n=21, **counts))
    for label, a, b in contrasts:
        for metric, test in metrics.items():
            paired.append(compare(label, model, data, a, b, metric, event=test))
for filename, rows in [("stand_decisions.csv", summary), ("stand_paired_outcomes.csv", paired)]:
    with (HERE / filename).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, list(dict.fromkeys(k for r in rows for k in r)), restval=0)
        writer.writeheader()
        writer.writerows(rows)
