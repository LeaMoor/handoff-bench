"""Statement with the key (cells 60-63) against the plain statement (34-35, 28-29).

Does the orchestrator pass on a key it can see? Old: every charge on the focal invoice was hidden
in 34, 35, 60, 61. New: only the first one is (DECISIONS, 2026-11-21); the pre world (34, 60) from
the v2 rerun, the post world (35, 61) from v3. 28, 29, 62, 63 are the same in both.
`claude_code`: Claude Code (Opus) as the orchestrator with a Haiku subagent, 60-63 only, new rule.

Usage: uv run python scripts/code_key.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal  # noqa: E402
from handoff_bench.analysis import report as r  # noqa: E402

PAIRS = {34: 60, 35: 61, 28: 62, 29: 63}  # plain statement → statement with the key
CALIBRATION = {"haiku": "07", "glm": "glm", "deepseek": "ds"}  # old calibration-hidden prefix
SHORT = {"haiku": "haiku", "glm": "glm", "deepseek": "ds", "claude_code": "cc"}
MAIN = [28, 29, 62, 63]


def sources(model: str, version: str) -> list[tuple[str, list[int]]]:
    """(run, cells) the table takes for this model in this version."""
    s = SHORT[model]
    if model == "claude_code":
        return [] if version == "old" else [("harness-cc-code-key", [60, 61, 62, 63])]
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
            path = Path("runs") / run
            df = r.load_results(path)
            for row in df[df.cell.isin(cells)].itertuples():
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
                        version=version,
                        situation={34: "pre", 60: "pre", 35: "post", 61: "post"}.get(
                            row.cell, row.fault
                        ),
                        channel=row.channel,
                        outcome=row.outcome,
                        duplicate=row.outcome == "duplicate",
                        delegated_again=again,
                        sub1_key=bool(k1),
                        key_forwarded=again
                        and bool(k1)
                        and k1 in delegates[1].args.get("goal", ""),
                        next_same_key=again and bool(k1) and first.get("sub-2") == k1,
                    )
                )
out = pd.DataFrame(rows)
g = out.groupby(["model", "situation", "channel", "version"])
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
    a.groupby(["model", "channel", "version"])[["key_forwarded", "next_same_key"]]
    .agg(["sum", "count"])
    .to_string()
)
