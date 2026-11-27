"""Descriptive summaries transcribed from pinned team reports. No episode-level inputs."""

import csv
from pathlib import Path

from pair_analysis import interval

here = Path(__file__).resolve().parent
rows = []


def add(series, commit, metric, group, k, n, *, unit="episode"):
    lo, hi = interval(k, n) if unit == "episode" else ("", "")
    rows.append((series, commit, metric, group, unit, k, n, round(100 * k / n, 2), lo, hi))


for group, k in [("B0", 10), ("delegated", 11), ("B1-clean", 15), ("B1-dirty", 14)]:
    add("M1 matched", "f6d331a", "duplicate", group, k, 36)
for group, k in [("B1-clean", 55), ("B1-dirty", 48)]:
    add("M1 full", "f6d331a", "duplicate", group, k, 126)
for group, k in [("overlap", 119), ("D12", 0), ("D12 owner-aware", 0)]:
    add("M8", "5a17790", "shared invoice duplicate", group, k, 126)
for group, k in [("D12", 46), ("D12 owner-aware", 29)]:
    add("M8", "5a17790", "parent unknown", group, k, 126)
for group, k, n in [
    ("visible k1", 0, 42),
    ("hidden k1", 26, 42),
    ("visible k2", 0, 30),
    ("hidden k2", 20, 30),
]:
    add("M3 matched stops", "1224790", "repaid", group, k, n, unit="payment")
for group, k in [
    ("Qwen", 54),
    ("GPT-4o-mini", 27),
    ("GLM", 13),
    ("Haiku", 0),
    ("Luna", 0),
    ("DeepSeek", 0),
]:
    add("M3 processing", "3938acd", "parent false paid", group, k, 63)
add("M3 capture", "d485219", "parent false paid", "Qwen", 27, 63)
with (here / "team_series.csv").open("w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(
        "series commit metric group unit events n percent wilson_low_pct wilson_high_pct".split()
    )
    writer.writerows(rows)
