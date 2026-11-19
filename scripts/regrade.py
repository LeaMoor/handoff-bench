"""Regrade night episodes with the current grader and coder (logs are not rerun)."""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.billing import EffectJournal
from handoff_bench.grading.coder import code_behaviour
from handoff_bench.grading.grader import grade
from handoff_bench.worlds import generate

rows = []
for run in sys.argv[1:]:
    old = pd.read_csv(Path(run) / "results.csv").set_index("episode_id")
    for ep in sorted(p for p in Path(run).iterdir() if p.is_dir()):
        cfg = json.loads((ep / "config.json").read_text())["episode"]
        tr = json.loads((ep / "trace.json").read_text())["orchestrator"]
        world = generate(cfg["world"], cfg["seed"], cfg["template"])
        stop = tr["stop_reason"]
        g = grade(
            EffectJournal.read(ep / "effects.jsonl"),
            world=world,
            finish_args=tr.get("finish_args"),
            stop_reason=stop,
        )
        final = tr["messages"][-1].get("content") if stop == "text_only" else None
        resp = [len(m["tool_calls"]) for m in tr["messages"] if m.get("tool_calls")]
        b = code_behaviour(
            CallJournal.read(ep / "calls.jsonl"),
            orchestrator="orchestrator",
            responses=resp,
            final_text=final,
        )
        o = old.loc[ep.name]
        rows.append(
            dict(
                run=Path(run).name.replace("prefreeze-", ""),
                episode=ep.name,
                cell=cfg["cell"],
                old_outcome=o.outcome,
                new_outcome=g.outcome,
                old_first=o.first_action,
                new_first=b.first_action,
                new_decision=b.decision,
                uncertain=" | ".join((tr.get("finish_args") or {}).get("uncertain") or []),
                claim=g.claim,
                effect=g.effect,
            )
        )
df = pd.DataFrame(rows)
df.to_csv("runs/night-regraded.csv", index=False)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 120)
print("outcome changes:")
print(pd.crosstab(df.old_outcome, df.new_outcome).to_string())
print("\nfirst action changes:")
print(pd.crosstab(df.old_first, df.new_first).to_string())
ch = df[df.old_outcome != df.new_outcome]
print(f"\n{len(ch)} changed outcomes:")
print(
    ch[["run", "episode", "claim", "effect", "old_outcome", "new_outcome", "uncertain"]].to_string(
        index=False
    )
)
