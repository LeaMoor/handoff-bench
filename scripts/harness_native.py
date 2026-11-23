"""Native cells (80-87): Claude Code's own subagents. Outcomes by world and key, and who made the
second charge: the same subagent retrying after the timeout, or a new one the orchestrator sent.

Usage: uv run python scripts/harness_native.py [runs/harness-cc-native]
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.agents.call_journal import CallJournal  # noqa: E402
from handoff_bench.analysis import report as r  # noqa: E402
from handoff_bench.worlds import generate  # noqa: E402

KEYS = ["none", "subagent", "task", "intent"]
run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/harness-cc-native")
d = r.load_results(run)
d = d[d["outcome"] != "invalid"]
rows = []
for e in d.itertuples():
    focal = generate(e.world, e.seed, e.template).focal_invoice
    calls = sorted(CallJournal.read(run / e.episode_id / "calls.jsonl"), key=lambda c: c.seq)
    charges = [c for c in calls if c.tool == "create_charge" and c.args.get("invoice_id") == focal]
    actors = [c.actor for c in charges]
    rows.append(
        dict(
            fault=e.fault,
            key=e.key,
            outcome=e.outcome,
            same_subagent_retry=len(actors) > len(set(actors)),
            new_subagent_charged=len(set(actors)) > 1,
            subagents=e.delegations,
        )
    )
t = pd.DataFrame(rows)
t["key"] = pd.Categorical(t["key"], KEYS, ordered=True)
pd.set_option("display.width", 200)
print(f"{len(t)} episodes\n")
print(pd.crosstab([t["fault"], t["key"]], t["outcome"], margins=True).to_string())
print("\nshare of episodes: the same subagent charged again / a second subagent charged")
print(
    t.groupby(["fault", "key"], observed=True)[["same_subagent_retry", "new_subagent_charged"]]
    .mean()
    .round(2)
    .to_string()
)
dup = t[t["outcome"] == "duplicate"]
print(
    f"\nduplicates: {len(dup)}; by the same subagent: {dup['same_subagent_retry'].sum()}, "
    f"by a second one: {dup['new_subagent_charged'].sum()}"
)
print(
    f"subagents per episode {t['subagents'].mean():.2f}; {d['prompt_tokens'].mean():.0f} prompt "
    f"tokens, {d['steps'].mean():.1f} orchestrator responses; API-equivalent ${d['cost'].sum():.2f}"
)
