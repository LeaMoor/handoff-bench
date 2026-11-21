"""M4 with the charge invisible (48-59): old hiding rule vs the final one, and Opus.

Usage: uv run python scripts/keys_rerun.py
"""

import sys
from glob import glob
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.analysis import report as r  # noqa: E402

# A transient provider 403 made most of this run invalid; its rerun replaces it.
BROKEN = {"prefreeze-4omini-keys-hidden"}
KEYS = ["none", "subagent", "orchestrator", "task", "intent", "pinned"]


def load(runs: list[str]) -> pd.DataFrame:
    """Valid M4 episodes (48-59) of these runs, one row per model and episode."""
    frames = []
    for run in runs:
        if Path(run).name in BROKEN or not Path(run, "results.csv").exists():
            continue
        df = r.load_results(Path(run))
        frames.append(df[df["cell"].between(48, 59)])
    df = pd.concat(frames, ignore_index=True)
    df["model"] = df["model"].str.split("/").str[-1]
    df = df[df["outcome"] != "invalid"].drop_duplicates(["model", "episode_id"])
    df["duplicate"] = df["outcome"] == "duplicate"
    df["unknown"] = df["outcome"] == "honest_unknown"
    df["key"] = pd.Categorical(df["key"], KEYS, ordered=True)
    return df


def by_key(df: pd.DataFrame, value: str, aggfunc: str = "mean") -> pd.DataFrame:
    return df.pivot_table(
        index=["fault", "key"], columns="model", values=value, aggfunc=aggfunc, observed=True
    )


old = load([p for p in glob("runs/prefreeze-*-keys-hidden*") if not p.endswith("-v3")])
new = load(glob("runs/prefreeze-*-keys-hidden-v3"))
opus = load(["runs/prefreeze-opus-cal-keys"])
pd.set_option("display.width", 250)
for name, df in (("old", old), ("new", new), ("opus", opus)):
    for value in ("duplicate", "unknown", "correct"):
        print(f"\n== {name}: {value}\n" + by_key(df, value).round(2).to_string())
    print(f"\n== {name}: n\n" + by_key(df, "cell", "size").to_string())
for name, df in (("old", old), ("new", new)):
    print(f"\n== {name}: outcomes by model\n" + pd.crosstab(df["model"], df["outcome"]).to_string())
