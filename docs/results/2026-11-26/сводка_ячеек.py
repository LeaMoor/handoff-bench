"""Descriptive statistics from local episode rows. Run with Python 3."""

import csv
from collections import defaultdict
from pathlib import Path

from анализ_пар import interval

HERE = Path(__file__).resolve().parent
RUNS = HERE.parents[2] / "runs"
groups = defaultdict(dict)
for path in sorted(RUNS.glob("*/results.csv")):
    run = path.parent.name
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            cell = int(row["cell"])
            if row["outcome"] == "invalid":
                continue
            series = []
            if run in {
                "prefreeze-03-calibration",
                "prefreeze-04-main-haiku",
                "prefreeze-05-main-luna",
            }:
                series.append("early_steps_3_5")
            if run.startswith("prefreeze-") and (6 <= cell <= 31 or 70 <= cell <= 73):
                series.append("grid")
            if "-keys-" not in run and (
                (run.endswith("-hidden-v2") and cell in (32, 34, 60))
                or (run.endswith("-hidden-v3") and cell in (33, 35, 61))
            ):
                series.append("calibration_fixed")
            if run.endswith("-code-key") and run.startswith("prefreeze-") and cell in (62, 63):
                series.append("statement_key")
            if run == "prefreeze-opus-cal-keys":
                series.append("opus_cal_keys")
            if run.endswith("keys-hidden-v3") and 48 <= cell <= 59:
                series.append("M4_fixed")
            if run in {
                "harness-cc-code-key",
                "harness-cc-keys-hidden",
                "harness-cc-native",
                "harness-hermes-native",
                "harness-codex-native",
            }:
                series.append(run)
            for name in series:
                key = (name, row["model"], cell, row["fault"], row.get("key", "subagent"))
                world = tuple(row[k] for k in ("world", "template", "seed", "repeat"))
                groups[key].setdefault(world, row)

metrics = {
    "duplicate": lambda r: r["outcome"] == "duplicate",
    "correct": lambda r: r["correct"] == "True",
    "honest_unknown_outcome": lambda r: r["outcome"] == "honest_unknown",
    "false_done_flag": lambda r: r["false_done"] == "True",
    "fault_fired": lambda r: r["fault_fired"] == "True",
}
with (HERE / "сводка_ячеек.csv").open("w", newline="") as stream:
    w = csv.writer(stream)
    w.writerow(
        [
            "series",
            "model",
            "cell",
            "fault",
            "key",
            "metric",
            "events",
            "valid",
            "percent",
            "wilson_low_pct",
            "wilson_high_pct",
        ]
    )
    for key, worlds in sorted(groups.items()):
        n = len(worlds)
        for metric, check in metrics.items():
            k = sum(check(row) for row in worlds.values())
            w.writerow([*key, metric, k, n, round(100 * k / n, 2), *interval(k, n)])
