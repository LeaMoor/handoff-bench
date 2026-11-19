"""Statement with the key (cells 60-63) against the plain statement (34-35, 28-29).

Does the orchestrator pass on a key it can see? Usage: uv run python scripts/code_key.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal  # noqa: E402
from handoff_bench.analysis import report as r  # noqa: E402

PAIRS = {34: 60, 35: 61, 28: 62, 29: 63}  # plain statement → statement with the key
RUNS = {
    "haiku": ["prefreeze-07-calibration-hidden", "prefreeze-haiku-code-key"],
    "glm": ["prefreeze-glm-calibration-hidden", "prefreeze-glm-code-key"],
    "deepseek": ["prefreeze-ds-calibration-hidden", "prefreeze-ds-code-key"],
}
CELLS = set(PAIRS) | set(PAIRS.values())

rows = []
for model, runs in RUNS.items():
    for run in runs:
        path = Path("runs") / run
        if not (path / "results.csv").exists():
            continue
        df = r.load_results(path)
        for row in df[df.cell.isin(CELLS)].itertuples():
            calls = sorted(
                CallJournal.read(path / row.episode_id / "calls.jsonl"), key=lambda c: c.seq
            )
            delegates = [c for c in calls if c.actor == "orchestrator" and c.tool == "delegate"]
            first = {}
            for c in calls:
                if c.tool == "create_charge" and c.actor.startswith("sub-"):
                    first.setdefault(c.actor, c.args.get("idempotency_key"))
            k1 = first.get("sub-1")
            again = len(delegates) >= 2
            rows.append(
                dict(
                    model=model,
                    situation={34: "pre", 60: "pre", 35: "post", 61: "post"}.get(
                        row.cell, row.fault
                    ),
                    channel=row.channel,
                    outcome=row.outcome,
                    duplicate=row.outcome == "duplicate",
                    delegated_again=again,
                    sub1_key=bool(k1),
                    key_forwarded=again and bool(k1) and k1 in delegates[1].args.get("goal", ""),
                    next_same_key=again and bool(k1) and first.get("sub-2") == k1,
                )
            )
out = pd.DataFrame(rows)
g = out.groupby(["model", "situation", "channel"])
t = pd.DataFrame(
    {
        "n": g.size(),
        "duplicate": g.duplicate.mean(),
        "unknown": g.outcome.apply(lambda s: (s == "honest_unknown").mean()),
        "eos": g.outcome.apply(lambda s: (s == "eos").mean()),
        "delegated_again": g.delegated_again.mean(),
        "sub1_key": g.sub1_key.mean(),
        "key_forwarded": g.key_forwarded.sum(),
        "next_same_key": g.next_same_key.sum(),
    }
)
pd.set_option("display.width", 220)
pd.set_option("display.precision", 2)
print(t.to_string())
a = out[out.delegated_again & out.sub1_key]
print("\nwhen the orchestrator delegated again after a keyed charge: key forwarded / reused")
print(
    a.groupby(["model", "channel"])[["key_forwarded", "next_same_key"]]
    .agg(["sum", "count"])
    .to_string()
)
