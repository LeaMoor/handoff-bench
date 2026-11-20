"""Calibration without the loophole (32-35) and statement with the key (60-61): old runs, where
every charge on the focal invoice was hidden, against the rerun, where only the first one is.

The pre world (32, 34, 60) comes from v2, the post world (33, 35, 61) from v3: v2 hid the first
subagent's charge, which in pre is the same as hiding nothing, but in post missed the fault when
the orchestrator first delegated a check (DECISIONS, 2026-11-21).

Usage: uv run python scripts/hidden_rerun.py
"""

import sys
from glob import glob
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.analysis import report as r  # noqa: E402

OLD = [
    "prefreeze-07-calibration-hidden",
    "prefreeze-qwen-calibration-hidden",
    "prefreeze-glm-calibration-hidden",
    "prefreeze-ds-calibration-hidden",
    "prefreeze-4omini-calibration-hidden",
    "prefreeze-luna-calibration-hidden",
    "prefreeze-haiku-code-key",
    "prefreeze-ds-code-key",
    "prefreeze-glm-code-key",
]
frames = []
PRE, POST = [32, 34, 60], [33, 35, 61]
for version, runs, cells in (
    ("old", OLD, PRE + POST),
    ("new", [Path(p).name for p in glob("runs/prefreeze-*-hidden-v2")], PRE),
    ("new", [Path(p).name for p in glob("runs/prefreeze-*-hidden-v3")], POST),
):
    for run in runs:
        df = r.load_results(Path("runs") / run)
        frames.append(df[df["cell"].isin(cells)].assign(version=version))
df = pd.concat(frames, ignore_index=True)
df = df[df["outcome"] != "invalid"]
df["model"] = df["model"].str.split("/").str[-1]
pd.set_option("display.width", 250)
for value, title in (
    (df["outcome"] == "duplicate", "duplicate"),
    (df["correct"].astype(bool), "correct"),
    (df["outcome"] == "honest_unknown", "honest_unknown"),
):
    print(f"\n{title}")
    t = df.assign(v=value).pivot_table(
        index=["fault", "channel"], columns=["model", "version"], values="v", aggfunc="mean"
    )
    print(t.round(2).to_string())
pair = df.assign(err=~df["correct"].astype(bool)).pivot_table(
    index="channel", columns=["model", "version"], values="err", aggfunc="mean"
)
print("\nerror averaged over the two worlds (pre, post)")
print(pair.round(2).to_string())
