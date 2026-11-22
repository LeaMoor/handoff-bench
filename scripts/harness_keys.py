"""M4 with the charge invisible (48-59): Claude Code as the orchestrator against our loop on the
same worlds (Opus with an Opus subagent, Haiku with a Haiku subagent).

Usage: uv run python scripts/harness_keys.py [runs/harness-cc-keys-hidden]
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.analysis import report as r  # noqa: E402

KEYS = ["none", "subagent", "orchestrator", "task", "intent", "pinned"]
VARIANT = ["cell", "template", "seed"]

cc = r.load_results(Path(sys.argv[1] if len(sys.argv) > 1 else "runs/harness-cc-keys-hidden"))
loops = {
    "opus loop": r.load_results(Path("runs/prefreeze-opus-cal-keys")),
    "haiku loop": r.load_results(Path("runs/prefreeze-haiku-keys-hidden-v3")),
}
cc = cc[cc["outcome"] != "invalid"]
worlds = cc[VARIANT].drop_duplicates()
frames = [cc.assign(who="claude code (opus) + haiku sub")]
for name, df in loops.items():
    same = df[df["cell"].between(48, 59) & (df["outcome"] != "invalid")].merge(worlds, on=VARIANT)
    frames.append(same.assign(who=name))
df = pd.concat(frames, ignore_index=True)
df["key"] = pd.Categorical(df["key"], KEYS, ordered=True)
pd.set_option("display.width", 220)
for value, title in (("duplicate", "duplicates"), ("honest_unknown", "honest unknown")):
    t = df.assign(v=df["outcome"] == value).pivot_table(
        index=["fault", "key"], columns="who", values="v", aggfunc="mean", observed=True
    )
    print(f"\n{title} (share of episodes)\n" + t.round(2).to_string())
n = df.pivot_table(
    index=["fault", "key"], columns="who", values="cell", aggfunc="size", observed=True
)
print("\nepisodes\n" + n.to_string())
outcomes = pd.crosstab([cc["fault"], cc["key"]], cc["outcome"])
print("\nclaude code: outcomes\n" + outcomes.to_string())
print(
    f"\nclaude code: {len(cc)} episodes, {cc['prompt_tokens'].mean():.0f} prompt tokens and "
    f"{cc['steps'].mean():.1f} responses per episode, API-equivalent cost ${cc['cost'].sum():.2f}"
)
