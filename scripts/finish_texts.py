"""Every episode's final report next to its grade: status, uncertain, message, effects.

Flags the episodes where the message to the user and the status field disagree."""

import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.analysis import report as r  # noqa: E402

SAYS_PAID = re.compile(
    r"\b(is|was|has been|have been|now|successfully)\s+(fully\s+)?"
    r"(paid|charged|settled|processed)\b"
    r"|payment (was |has been )?(successful|completed|succeeded)|\bpaid in full\b",
    re.I,
)
SAYS_NOT_PAID = re.compile(
    r"\bnot (been )?(paid|processed|charged|completed|go through|went through)\b|\bfailed\b"
    r"|\bno (charge|payment)s? (was|were|has been|have been) (made|created|recorded)\b"
    r"|\bstill open\b|\bunpaid\b",
    re.I,
)
SAYS_UNSURE = re.compile(
    r"\b(unknown|unclear|uncertain|could not (confirm|verify|determine)"
    r"|can'?t (confirm|tell|verify)"
    r"|cannot (confirm|verify|determine)|not (sure|confirmed|verified)|timed? ?out|pending)\b",
    re.I,
)


def stance(text: str) -> str:
    paid, not_paid, unsure = (bool(p.search(text)) for p in (SAYS_PAID, SAYS_NOT_PAID, SAYS_UNSURE))
    if unsure:
        return "unsure"
    if paid and not not_paid:
        return "paid"
    if not_paid and not paid:
        return "not_paid"
    return "mixed" if paid else "none"


rows = []
for run in sorted(Path("runs").glob("prefreeze-*")):
    df = r.load_results(run)
    for row in df.itertuples():
        ep = run / row.episode_id
        tr = json.loads((ep / "trace.json").read_text())["orchestrator"]
        fa = tr.get("finish_args") or {}
        text = fa.get("message") if fa else (tr["messages"][-1].get("content") or "")
        rows.append(
            dict(
                run=run.name,
                episode=row.episode_id,
                model=row.model,
                cell=row.cell,
                block=row.block,
                fault=row.fault,
                outcome=row.outcome,
                claim=row.claim,
                effect=row.effect,
                focal_charges=row.focal_charges,
                uncertain=" | ".join(fa.get("uncertain") or []),
                message=text or "",
                text_stance=stance(text or ""),
            )
        )
out = pd.DataFrame(rows)
out.to_csv("runs/finish-texts.csv", index=False)
print(len(out), "episodes")
