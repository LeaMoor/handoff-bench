"""M7: what each role actually got in the given runs (model, prompt, tools, task, step budget).

Usage: uv run python scripts/role_parity.py runs/<run> [runs/<run> ...]
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
from handoff_bench.analysis.parity import parity_table, role_rows, served_mismatches  # noqa: E402


def main(paths: list[str]) -> None:
    df = role_rows([Path(p) for p in paths])
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_colwidth", 60)
    print(f"{df.groupby('run')['episode_id'].nunique().sum()} episodes, {len(df)} actors\n")
    print(parity_table(df).round(3).to_string(), "\n")
    mismatches = served_mismatches(df)
    print(
        f"episodes where the roles were served by different models or providers: {len(mismatches)}"
    )
    print(mismatches.to_string() if len(mismatches) else "", "\n")
    sub = df[df["role"] == "subagent"]
    print("subagent goals that ask to act once, without the stand's rules (by rules on/off):")
    print(
        sub.pivot_table(index="configured_model", columns="rules", values="asks_once").round(3),
        "\n",
    )
    print("subagent runs that hit the step limit, by cell:")
    hit = sub[sub["stop_reason"] == "max_steps"]
    print(hit.groupby(["configured_model", "cell"]).size().to_string() if len(hit) else "none")


if __name__ == "__main__":
    main(sys.argv[1:])
