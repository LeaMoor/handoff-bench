"""Compact view of one episode: every tool call in seq order, what came back, final finish."""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal


def short(x, n=160):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = s.replace("\n", " | ")
    return s if len(s) <= n else s[:n] + "…"


for d in sys.argv[1:]:
    ep = Path(d)
    cfg = json.loads((ep / "config.json").read_text())["episode"]
    res = json.loads((ep / "result.json").read_text())
    trace = json.loads((ep / "trace.json").read_text())
    print(
        f"=== {ep.name}  fault={cfg['fault']} channel={cfg['channel']}  -> "
        f"{res['outcome']} ({res['first_action']} / {res['decision']})"
    )
    print("  task:", short(trace["orchestrator"]["messages"][1]["content"], 200))
    for r in sorted(CallJournal.read(ep / "calls.jsonl"), key=lambda r: r.seq):
        out = r.result if r.outcome == "ok" else f"{r.outcome} {r.error_code or ''}"
        print(
            f"  {r.seq:>2} {r.actor:12} {r.tool:14} {short(r.args, 110):110}  => {short(out, 140)}"
        )
    fa = trace["orchestrator"].get("finish_args")
    if fa:
        print("  finish:", short(fa, 300))
    print()
