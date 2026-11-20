"""Does a subagent's idempotency key cross the boundary through the trace?

For every episode where the orchestrator itself delegated again after a subagent tried to pay
the focal invoice: was the first key in the subagent's report, did the orchestrator put it into
the next goal, and did the next subagent use it? Usage: uv run python scripts/key_chain.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal  # noqa: E402

rows = []
for run in sorted(Path("runs").glob("prefreeze-*")):
    for ep in sorted(p for p in run.iterdir() if (p / "result.json").exists()):
        cfg = json.loads((ep / "config.json").read_text())["episode"]
        if cfg.get("key", "subagent") != "subagent":
            continue  # the harness set the key there; here only the models carry it
        trace = json.loads((ep / "trace.json").read_text())
        calls = sorted(CallJournal.read(ep / "calls.jsonl"), key=lambda c: c.seq)
        delegates = [c for c in calls if c.actor == "orchestrator" and c.tool == "delegate"]
        if len(delegates) < 2:
            continue
        charges = {}
        for c in calls:
            if c.tool == "create_charge" and c.actor.startswith("sub-"):
                charges.setdefault(c.actor, c.args.get("idempotency_key"))
        k1 = charges.get("sub-1")
        if not k1:
            continue
        report = json.dumps(
            (trace["subagents"][0] or {}).get("finish_args") or {}, ensure_ascii=False
        )
        seen = next((c.result for c in delegates[:1]), {}) or {}
        shown = json.dumps(seen, ensure_ascii=False)  # what the orchestrator got back
        goal2 = delegates[1].args.get("goal", "")
        k2 = charges.get("sub-2")
        rows.append(
            dict(
                model=(cfg.get("model") or {}).get("model", "?").split("/")[-1],
                cell=cfg["cell"],
                block=cfg["block"],
                channel=cfg["channel"],
                key_in_report=k1 in report,
                key_reached_orchestrator=k1 in shown,
                key_in_next_goal=k1 in goal2,
                next_paid=k2 is not None or "sub-2" in charges,
                next_same_key=k2 == k1,
            )
        )
df = pd.DataFrame(rows)
pd.set_option("display.width", 220)
pd.set_option("display.precision", 2)
print(
    len(df), "episodes where a subagent paid with its own key and the orchestrator delegated again"
)
g = df.groupby("model")
print(
    pd.DataFrame(
        {
            "n": g.size(),
            "key in report": g.key_in_report.mean(),
            "reached orchestrator": g.key_reached_orchestrator.mean(),
            "put in next goal": g.key_in_next_goal.mean(),
            "next subagent paid": g.next_paid.mean(),
            "next used same key": g.next_same_key.mean(),
        }
    ).to_string()
)
print("\nby channel (all models):")
g = df.groupby("channel")
print(
    pd.DataFrame(
        {
            "n": g.size(),
            "reached orchestrator": g.key_reached_orchestrator.mean(),
            "put in next goal": g.key_in_next_goal.mean(),
            "next used same key": g.next_same_key.mean(),
        }
    ).to_string()
)
print("\nchain: reached orchestrator → in next goal → same key used")
print(pd.crosstab([df.key_reached_orchestrator, df.key_in_next_goal], df.next_same_key).to_string())
