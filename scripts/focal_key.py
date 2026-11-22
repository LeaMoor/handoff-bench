"""Statement with the key (60-63) against the plain statement (34-35, 28-29): when the
orchestrator hands the focal invoice out again after the first subagent paid it with its own key,
does the key go into the new goal?

Only re-delegations that name the focal invoice count. `code_key.py` took the second delegation
whatever it was for, and in template 2 that is usually the other invoice. Old and new hiding rule
as in `code_key.py` (DECISIONS, 2026-11-21).

Usage: uv run python scripts/focal_key.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal  # noqa: E402
from handoff_bench.worlds import generate  # noqa: E402

CALIBRATION = {"haiku": "07", "glm": "glm", "deepseek": "ds"}  # old calibration-hidden prefix
SHORT = {"haiku": "haiku", "glm": "glm", "deepseek": "ds"}
MAIN = [28, 29, 62, 63]
KEYED = {60, 61, 62, 63}


def sources(model: str, version: str) -> list[tuple[str, list[int]]]:
    s = SHORT[model]
    if version == "old":
        return [
            (f"prefreeze-{CALIBRATION[model]}-calibration-hidden", [34, 35]),
            (f"prefreeze-{s}-code-key", [60, 61, *MAIN]),
        ]
    return [
        (f"prefreeze-{s}-hidden-v2", [34, 60]),
        (f"prefreeze-{s}-hidden-v3", [35, 61]),
        (f"prefreeze-{s}-code-key", MAIN),
    ]


rows = []
for model in SHORT:
    for version in ("old", "new"):
        for run, cells in sources(model, version):
            d = pd.read_csv(Path("runs", run, "results.csv"))
            for r in d[d.cell.isin(cells) & (d.outcome != "invalid")].itertuples():
                focal = generate(r.world, r.seed, r.template).focal_invoice
                path = Path("runs", run, r.episode_id, "calls.jsonl")
                calls = sorted(CallJournal.read(path), key=lambda c: c.seq)
                goals = [
                    c.args.get("goal", "")
                    for c in calls
                    if c.actor == "orchestrator" and c.tool == "delegate"
                ]
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
                again = [g for g in goals[1:] if focal in g]
                rows.append(
                    dict(
                        model=model,
                        version=version,
                        statement="with key" if r.cell in KEYED else "plain",
                        cell=r.cell,
                        again=bool(again and k1),
                        forwarded=bool(again and k1) and any(k1 in g for g in again),
                    )
                )
t = pd.DataFrame(rows)
a = t[t.again]
pd.set_option("display.width", 220)
print("re-delegations of the focal invoice after sub-1 paid it with its own key:")
print("the key in a new goal / such episodes\n")
print(
    a.groupby(["model", "statement", "version"])
    .forwarded.agg(["sum", "count"])
    .unstack("version")
    .to_string()
)
print("\nstatement with the key, new rule, by cell")
keyed = a[(a.statement == "with key") & (a.version == "new")]
print(keyed.groupby(["model", "cell"]).forwarded.agg(["sum", "count"]).unstack("cell").to_string())
