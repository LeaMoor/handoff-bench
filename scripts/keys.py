"""M4: who sets the idempotency key, and whether the repeat became a duplicate.

Usage: uv run python scripts/keys.py runs/prefreeze-*-keys
"""

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal  # noqa: E402
from handoff_bench.analysis import report as r  # noqa: E402

GOAL_KEY = re.compile(r"idempotency[_ ]key\W*[:=]?\s*`?([A-Za-z0-9_.:\-]{4,})`?", re.I)

rows = []
for run in sys.argv[1:]:
    df = r.load_results(Path(run))
    for row in df.itertuples():
        calls = sorted(
            CallJournal.read(Path(run) / row.episode_id / "calls.jsonl"), key=lambda c: c.seq
        )
        goals = [
            c.args.get("goal", "")
            for c in calls
            if c.actor == "orchestrator" and c.tool == "delegate"
        ]
        goal_keys = {m.group(1) for g in goals for m in GOAL_KEY.finditer(g)}
        charges = [c for c in calls if c.tool == "create_charge" and c.actor.startswith("sub-")]
        model_keys = [c.args.get("idempotency_key") for c in charges]
        refused = [c for c in charges if c.error_code == "idempotency_error"]
        after_refusal_new_key = (
            any(
                c.seq > refused[0].seq
                and c.outcome == "ok"
                and c.args.get("idempotency_key") != refused[0].args.get("idempotency_key")
                for c in charges
            )
            if refused
            else False
        )
        first_by_sub = {}
        for c in charges:
            first_by_sub.setdefault(c.actor, c.args.get("idempotency_key"))
        subs = list(first_by_sub.values())
        rows.append(
            dict(
                model=row.model.split("/")[-1],
                check="impossible" if row.delay == "long" else "possible",
                fault=row.fault,
                key=row.key,
                outcome=row.outcome,
                duplicate=row.outcome == "duplicate",
                eos=row.outcome == "eos",
                keyed=bool(model_keys) and all(k for k in model_keys),
                goal_has_key=bool(goal_keys),
                kept_goal_key=bool(goal_keys) and any(k in goal_keys for k in model_keys if k),
                same_key_across_subagents=len(subs) >= 2
                and subs[0] is not None
                and len(set(subs)) == 1,
                refused=bool(refused),
                cascade=after_refusal_new_key,
            )
        )
out = pd.DataFrame(rows)
order = ["none", "subagent", "orchestrator", "task", "intent", "pinned"]
out["key"] = pd.Categorical(out["key"], order, ordered=True)
g = out.groupby(["check", "model", "fault", "key"], observed=True)
t = pd.DataFrame(
    {
        "n": g.size(),
        "duplicate": g["duplicate"].mean(),
        "eos": g["eos"].mean(),
        "model_passed_key": g["keyed"].mean(),
        "same_key_2_subagents": g["same_key_across_subagents"].mean(),
        "goal_has_key": g["goal_has_key"].mean(),
        "kept_goal_key": g["kept_goal_key"].mean(),
        "refused": g["refused"].mean(),
        "cascade": g["cascade"].mean(),
    }
)
pd.set_option("display.width", 250)
pd.set_option("display.precision", 2)
pd.set_option("display.max_rows", 200)
print(t.to_string())
print("\nduplicates by key source (both failures), check possible / impossible:")
print(
    out.pivot_table(
        index="key", columns=["check", "model"], values="duplicate", aggfunc="mean", observed=True
    )
    .round(2)
    .to_string()
)
