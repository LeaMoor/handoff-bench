"""Calibration episodes: what each delegation after the empty report did and saw, and the finish."""

import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal

rows = []
for run in sys.argv[1:]:
    for ep in sorted(p for p in Path(run).iterdir() if p.is_dir()):
        cfg = json.loads((ep / "config.json").read_text())["episode"]
        if cfg["block"] != "calibration":
            continue
        calls = sorted(CallJournal.read(ep / "calls.jsonl"), key=lambda r: r.seq)
        tr = json.loads((ep / "trace.json").read_text())["orchestrator"]
        dels = [
            r
            for r in calls
            if r.actor == "orchestrator" and r.tool == "delegate" and r.outcome == "ok"
        ]
        later = []
        for i, d in enumerate(dels[1:], 2):
            sub = [r for r in calls if r.actor == f"sub-{i}"]
            charged = any(r.tool == "create_charge" for r in sub)
            seen_status = [
                r.result["status"]
                for r in sub
                if r.tool == "get_invoice"
                and r.outcome == "ok"
                and r.args.get("invoice_id") == cfg.get("focal", None)
            ] or [
                r.result.get("status") for r in sub if r.tool == "get_invoice" and r.outcome == "ok"
            ]
            seen_charges = [
                len(r.result) for r in sub if r.tool == "list_charges" and r.outcome == "ok"
            ]
            goal = d.args.get("goal", "")
            readonly_words = bool(
                re.search(
                    (
                        r"read[- ]only|do not (pay|create|charge|make)|don'?t (pay|charge)"
                        r"|no (payment|changes)|only (look|check)|verify"
                        r"|check (the )?status|look up"
                    ),
                    goal,
                    re.I,
                )
            )
            later.append(
                dict(
                    kind="pay" if charged else "check",
                    readonly_words=readonly_words,
                    saw_status=",".join(sorted(set(map(str, seen_status)))) or "-",
                    saw_charges=max(seen_charges) if seen_charges else None,
                )
            )
        fa = tr.get("finish_args") or {}
        rows.append(
            dict(
                run=Path(run).name,
                episode=ep.name,
                cell=cfg["cell"],
                fault=cfg["fault"],
                channel=cfg["channel"],
                n_delegations=len(dels),
                second=later[0]["kind"] if later else "none",
                second_readonly_words=later[0]["readonly_words"] if later else None,
                check_saw=(later[0]["saw_status"] + f"/{later[0]['saw_charges']}")
                if later and later[0]["kind"] == "check"
                else "-",
                then=("+".join(x["kind"] for x in later[1:]) or "-"),
                finish=fa.get("status", tr["stop_reason"]),
            )
        )
df = pd.DataFrame(rows)
pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 200)
print("second delegation, by cell:")
print(pd.crosstab([df.cell, df.fault, df.channel], df.second).to_string())
c = df[df.second == "check"]
print("\ncoder 'check' vs read-only wording in the goal:")
print(pd.crosstab(c.second_readonly_words, c.second).to_string())
print("\nwhat the check saw (status/charges) → what the orchestrator did next and how it finished:")
print(pd.crosstab([c.fault, c.check_saw], [c.then, c.finish]).to_string())
print("\nno check: second delegation pays or none → finish:")
o = df[df.second != "check"]
print(pd.crosstab([o.fault, o.channel, o.second], o.finish).to_string())
df.to_csv(Path(sys.argv[1]).parent / "calibration-trace.csv", index=False)
