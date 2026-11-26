"""Main grid (cells 6-31, hedged statement 70-73) for every model: outcome shares by condition.

Usage: uv run python scripts/grid.py [runs/prefreeze-*]
"""

import sys
from glob import glob
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.analysis import report as r  # noqa: E402

runs = [Path(p) for p in (sys.argv[1:] or sorted(glob("runs/prefreeze-*")))]
frames = []
for run in runs:
    if not (run / "results.csv").exists():
        continue
    df = r.load_results(run)
    grid_cells = df["cell"].between(6, 31) | df["cell"].between(70, 73)
    # The historical M8 pilot also used 70-72.
    frames.append(df[df["block"].eq("main") & grid_cells])
df = pd.concat(frames, ignore_index=True)
df["model"] = df["model"].str.split("/").str[-1]
# Cells 28-29 were also run next to code_key: the same episode twice, keep one valid copy.
df = df[df["outcome"] != "invalid"]
df = df.drop_duplicates(["model", "episode_id"])
df["duplicate"] = df["outcome"] == "duplicate"
df["false_done"] = (df["claim"] == "done") & (df["effect"] == "skip")
df["honest_unknown"] = df["outcome"] == "honest_unknown"
df["condition"] = (
    df["agent"].map({"single": "B0", "delegated": "deleg"})
    + "/"
    + df["delay"]
    + "/"
    + df["rules"].map({True: "rules", False: "no-rules"})
)
pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 200)
for value in ("correct", "duplicate", "false_done", "honest_unknown", "self_checked"):
    print(f"\n{value} by condition and channel (n per cell in the last table)")
    t = df.pivot_table(
        index=["condition", "channel"], columns="model", values=value, aggfunc="mean"
    )
    print(t.round(2).to_string())
print("\nn")
print(
    df.pivot_table(
        index=["condition", "channel"], columns="model", values="cell", aggfunc="size"
    ).to_string()
)

# Each world has its own failure: committed -> duplicate, lost -> "done" with nothing charged.
df["world_error"] = df["duplicate"].where(df["fault"] == "timeout_committed", df["false_done"])
print("\nerror in its own world: duplicate (committed) / false done (lost)")
t = df.pivot_table(
    index=["condition", "channel"], columns=["fault", "model"], values="world_error", aggfunc="mean"
)
print(t.round(2).to_string())
