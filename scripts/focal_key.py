"""Statement with the key (60-63): when the orchestrator hands the focal invoice out again after
the first subagent paid it with its own key, does the key go into the new goal?

Only re-delegations that name the focal invoice count: in template 2 the second delegation is
usually the other invoice, and `code_key.py` counted it too. Our loop on the fixed hiding rule
(60 from hidden-v2, 61 from hidden-v3, 62-63 from code-key) and Claude Code (Opus).

Usage: uv run python scripts/focal_key.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal  # noqa: E402
from handoff_bench.worlds import generate  # noqa: E402

SRC = {
    "claude_code": [("harness-cc-code-key", [60, 61, 62, 63])],
    **{
        m: [
            (f"prefreeze-{s}-hidden-v2", [60]),
            (f"prefreeze-{s}-hidden-v3", [61]),
            (f"prefreeze-{s}-code-key", [62, 63]),
        ]
        for m, s in (("haiku", "haiku"), ("deepseek", "ds"), ("glm", "glm"))
    },
}
rows = []
for model, srcs in SRC.items():
    for run, cells in srcs:
        d = pd.read_csv(f"runs/{run}/results.csv")
        for r in d[d.cell.isin(cells) & (d.outcome != "invalid")].itertuples():
            focal = generate(r.world, r.seed, r.template).focal_invoice
            calls = sorted(
                CallJournal.read(Path("runs", run, r.episode_id, "calls.jsonl")),
                key=lambda c: c.seq,
            )
            dl = [c for c in calls if c.actor == "orchestrator" and c.tool == "delegate"]
            k1 = next(
                (
                    c.args.get("idempotency_key")
                    for c in calls
                    if c.actor == "sub-1"
                    and c.tool == "create_charge"
                    and c.args.get("invoice_id") == focal
                ),
                None,
            )
            again = [c for c in dl[1:] if focal in c.args.get("goal", "")]
            rows.append(
                dict(
                    model=model,
                    cell=r.cell,
                    dup=r.outcome == "duplicate",
                    k1=bool(k1),
                    again=bool(again),
                    fwd=bool(again)
                    and bool(k1)
                    and any(k1 in c.args.get("goal", "") for c in again),
                )
            )
t = pd.DataFrame(rows)
a = t[t.again & t.k1]
print("focal re-delegations after sub-1 paid it with its own key: forwarded / total")
print(a.groupby("model").fwd.agg(["sum", "count"]))
print(a.groupby(["model", "cell"]).fwd.agg(["sum", "count"]).unstack("cell"))
print("\nshare of episodes with a focal re-delegation, and duplicates")
print(t.groupby(["model", "cell"])[["again", "dup"]].mean().round(2).unstack("cell"))
