"""Regrade night episodes with the current grader and coder (logs are not rerun)."""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.tools import INVALID_STOPS
from handoff_bench.billing import EffectJournal
from handoff_bench.billing.models import Invoice
from handoff_bench.grading.coder import code_behaviour
from handoff_bench.grading.grader import grade
from handoff_bench.grading.ownership import grade_m8
from handoff_bench.worlds import World, generate


def episode_world(config: dict[str, Any], episode: Path) -> World:
    """Load the archived worklists and invoices for the historical template-2 M8 pilot."""
    cfg = config["episode"]
    world = generate(cfg["world"], cfg["seed"], cfg["template"])
    if cfg.get("block") != "ownership" or cfg["template"] != 2:
        return world
    lists = config["worklists"]
    targets = list(dict.fromkeys([*lists["work-a"], *lists["work-b"]]))
    with closing(sqlite3.connect(f"{(episode / 'bank.sqlite').as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        invoices = [
            Invoice.model_validate(dict(row)) for row in db.execute("SELECT * FROM invoices")
        ]
    return world.model_copy(
        update={"invoices": invoices, "targets": targets, "focal_invoice": targets[1]}
    )


rows = []
for run in sys.argv[1:]:
    old = pd.read_csv(Path(run) / "results.csv").set_index("episode_id")
    unrecorded = {p.name for p in Path(run).iterdir() if p.is_dir()} - set(old.index)
    if unrecorded:
        print(f"{run}: skipped {len(unrecorded)} directories without results")
    for episode_id, recorded in old.sort_index().iterrows():
        ep = Path(run) / episode_id
        config = json.loads((ep / "config.json").read_text())
        cfg = config["episode"]
        trace = json.loads((ep / "trace.json").read_text())
        tr = trace["orchestrator"]
        # Historical M8 cells 70-72 now belong to the hedged statement experiment.
        is_ownership = cfg.get("block") == "ownership"
        world = episode_world(config, ep.resolve())
        stop = tr["stop_reason"]
        effects = EffectJournal.read(ep / "effects.jsonl")
        calls = CallJournal.read(ep / "calls.jsonl")
        finish = tr.get("finish_args")
        final = tr["messages"][-1].get("content") if stop == "text_only" else None
        ownership = None
        if is_ownership:
            children = trace["subagents"]
            child_stop = next(
                (
                    child["stop_reason"]
                    for child in children
                    if child["stop_reason"] in INVALID_STOPS
                ),
                stop,
            )
            g, ownership = grade_m8(
                effects,
                calls,
                world=world,
                finish_args=finish,
                stop_reason=child_stop,
                final_text=final,
                concurrent=trace["parallel_overlap"],
                child_actors=set(trace["tasks"]),
            )
        else:
            g = grade(
                effects,
                world=world,
                finish_args=finish,
                stop_reason=(
                    recorded.stop_reason if cfg.get("block") == "partial_execution" else stop
                ),
                whole_batch=cfg.get("block") == "partial_execution",
                final_text=final,
            )
        resp = [len(m["tool_calls"]) for m in tr["messages"] if m.get("tool_calls")]
        b = code_behaviour(calls, orchestrator="orchestrator", responses=resp, final_text=final)
        row = dict(
            run=Path(run).name.replace("prefreeze-", ""),
            episode=ep.name,
            cell=cfg["cell"],
            old_outcome=recorded.outcome,
            new_outcome=g.outcome,
            old_first=recorded.first_action,
            new_first=b.first_action,
            new_decision=b.decision,
            uncertain=" | ".join((finish or {}).get("uncertain") or []),
            claim=g.claim,
            effect=g.effect,
        )
        if ownership is not None:
            row["ownership"] = ownership.model_dump_json()
        rows.append(row)
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
