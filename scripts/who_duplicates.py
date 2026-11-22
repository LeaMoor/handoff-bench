"""Who made the second charge in a duplicate: the subagent that already charged, or a new one.

Reads the effect journal. Usage: uv run python scripts/who_duplicates.py
"""

import json
import sys
from glob import glob
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.analysis import report as r  # noqa: E402

# Long delay: text, code and the hedged statement.
CELLS = [18, 19, 22, 23, 24, 25, 28, 29, 70, 71, 72, 73]
rows = []
for run in sorted(glob("runs/prefreeze-*")):
    if not Path(run, "results.csv").exists():
        continue
    df = r.load_results(Path(run))
    df = df[
        df["cell"].isin(CELLS) & (df["fault"] == "timeout_committed") & (df["outcome"] != "invalid")
    ]
    for e in df.itertuples():
        effects = [
            json.loads(line)
            for line in Path(run, e.episode_id, "effects.jsonl").read_text().splitlines()
        ]
        # Template 2 pays two invoices: only charges of one invoice can duplicate each other.
        by_invoice: dict[str, list[str]] = {}
        for f in effects:
            by_invoice.setdefault(f["invoice_id"], []).append(f["actor"])
        repeated = next((a for a in by_invoice.values() if len(a) >= 2), None)
        who = "none" if repeated is None else "same" if repeated[1] == repeated[0] else "new"
        rows.append(
            dict(model=e.model.split("/")[-1], channel=e.channel, episode=e.episode_id, who=who)
        )
out = pd.DataFrame(rows).drop_duplicates(["model", "episode"])
pd.set_option("display.width", 250)
print("share of committed-world episodes (both rules settings) by who made the 2nd charge")
t = pd.crosstab([out["channel"], out["who"]], out["model"], normalize=False)
n = out.groupby(["channel", "model"]).size().unstack()
print((t / n.reindex(t.index.get_level_values(0)).set_axis(t.index)).round(2).to_string())
