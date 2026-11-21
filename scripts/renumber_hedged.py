"""One-off: the hedged statement runs recorded cells 64-67, which M3 processing took; rewrite
them as 70-73 (DECISIONS, 2026-11-20). Only `cell` changes: episode ids and folders keep the
old `c64-`..`c67-` prefix. Safe to run twice.

Usage: uv run python scripts/renumber_hedged.py [runs/prefreeze-*-hedged]
"""

import json
import sys
from glob import glob
from pathlib import Path

import pandas as pd

OLD_TO_NEW = {64: 70, 65: 71, 66: 72, 67: 73}

for run in [Path(p) for p in (sys.argv[1:] or sorted(glob("runs/prefreeze-*-hedged")))]:
    csv = run / "results.csv"
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    old = (df["channel"] == "code_hedged") & df["cell"].isin([str(c) for c in OLD_TO_NEW])
    df.loc[old, "cell"] = df.loc[old, "cell"].map(lambda c: str(OLD_TO_NEW[int(c)]))
    df.to_csv(csv, index=False)
    for episode in df.loc[df["channel"] == "code_hedged", "episode_id"]:
        for name, path in (("config.json", ("episode",)), ("result.json", ())):
            file = run / episode / name
            data = json.loads(file.read_text(encoding="utf-8"))
            node = data
            for key in path:
                node = node[key]
            node["cell"] = OLD_TO_NEW.get(node["cell"], node["cell"])
            file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{run.name}: {int(old.sum())} rows renumbered")
