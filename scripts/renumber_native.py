"""One-off: the native runs recorded cells 80-87, which open PRs took (M3 partial execution,
M2 retell); rewrite them as 86-93 (DECISIONS, 2026-11-23). The cell, the episode id and the
folder all change. Safe to run twice: a run whose native cells already start at 86 is skipped.

Usage: uv run python scripts/renumber_native.py [runs/harness-cc-native*]
"""

import re
import sys
from glob import glob
from pathlib import Path

import pandas as pd

SHIFT = 6
OLD = range(80, 88)
# Files that name the episode: its id, its folder in a path, or `"cell": N`.
FILES = (
    "config.json",
    "result.json",
    "calls.jsonl",
    "stand_calls.jsonl",
    "effects.jsonl",
    "hook_policy.json",
)


def rewrite(text: str) -> str:
    text = re.sub(r"\bc(8[0-7])-w", lambda m: f"c{int(m[1]) + SHIFT}-w", text)
    return re.sub(r'"cell": (8[0-7])\b', lambda m: f'"cell": {int(m[1]) + SHIFT}', text)


for run in [Path(p) for p in (sys.argv[1:] or sorted(glob("runs/harness-cc-native*/")))]:
    csv = run / "results.csv"
    df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    native = df["block"] == "native"
    if df.loc[native, "cell"].astype(int).min() >= OLD.start + SHIFT:
        print(f"{run.name}: already renumbered")
        continue
    # Highest first: 87 -> 93 frees 87 before 81 -> 87 takes it.
    for old in reversed(OLD):
        for folder in sorted(run.glob(f"c{old}-w*")):
            for name in FILES:
                file = folder / name
                if file.exists():
                    file.write_text(rewrite(file.read_text(encoding="utf-8")), encoding="utf-8")
            folder.rename(run / rewrite(folder.name))
    df.loc[native, "cell"] = df.loc[native, "cell"].map(lambda c: str(int(c) + SHIFT))
    df.loc[native, "episode_id"] = df.loc[native, "episode_id"].map(rewrite)
    df.to_csv(csv, index=False)
    print(f"{run.name}: {int(native.sum())} rows renumbered")
