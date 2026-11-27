"""M7: what each role actually got (model, prompt, task, tools, step budget), read from the
episode logs rather than from the code."""

import json
import re
from pathlib import Path

import pandas as pd

from handoff_bench.agents.loop import ModelCallLog
from handoff_bench.jsonl import read_rows

# The goal itself asks to act once: the stand's rules say so, but an orchestrator can write it too.
ONCE = re.compile(r"\bonce\b|duplicat|idempoten|double[- ]charg|charge again", re.I)


def role_of(actor: str) -> str:
    """orchestrator / subagent / retell, from the actor name in the logs."""
    if actor.startswith("retell-"):
        return "retell"
    if actor.startswith("sub-"):
        return "subagent"
    return actor


def _stop_reasons(trace: dict) -> dict[str, str]:
    agents = [trace["orchestrator"], *trace.get("subagents", []), *trace.get("retells", [])]
    return {a["actor"]: a["stop_reason"] for a in agents}


def _served(responses: list[dict], field: str) -> str | None:
    """Like `runner.run._served`: missing values are skipped; None if nothing was served."""
    return "|".join(sorted({str(r[field]) for r in responses if r.get(field)})) or None


def _flag(value: bool, applies: bool) -> float:
    return float(value) if applies else float("nan")


def episode_roles(episode_dir: Path) -> list[dict]:
    """One row per actor of the episode: what went into its model and how its run ended."""
    config = json.loads((episode_dir / "config.json").read_text())["episode"]
    agents = config["agents"]
    stops = _stop_reasons(json.loads((episode_dir / "trace.json").read_text()))
    calls: dict[str, list[ModelCallLog]] = {}
    for row in sorted(
        read_rows(episode_dir / "model_calls.jsonl", ModelCallLog), key=lambda r: r.step
    ):
        calls.setdefault(row.actor, []).append(row)
    out = []
    for actor, rows in calls.items():
        role = role_of(actor)
        first = {m["role"]: m.get("content") or "" for m in rows[0].new_messages}
        task = first.get("user", "")
        rules = agents["subagent_rules"]
        responses = [r.response for r in rows if r.response]
        settings = (
            config["model"]
            if role == "orchestrator"
            else (config.get("subagent_model") or config["model"])
        )
        is_goal = role in ("orchestrator", "subagent")  # a retell's input is a transcript
        out.append(
            {
                "run": episode_dir.parent.name,
                "episode_id": config["episode_id"],
                "cell": config["cell"],
                "rules": config["rules"],
                "configured_model": (settings or {}).get("model", "unknown"),
                "role": role,
                "actor": actor,
                "served_model": _served(responses, "model"),
                "served_provider": _served(responses, "provider"),
                "system_prompt": first.get("system", ""),
                "tools": ",".join(sorted({t for r in rows for t in r.tools})),
                "task_chars": len(task),
                "stand_rules": _flag(role == "subagent" and bool(rules) and rules in task, is_goal),
                "asks_once": _flag(
                    bool(ONCE.search(task.replace(rules, "") if rules else task)), is_goal
                ),
                "steps": len(rows),
                "max_steps": {
                    "orchestrator": agents["orchestrator_max_steps"],
                    "subagent": agents["subagent_max_steps"],
                }.get(role, 1),
                "stop_reason": stops.get(actor, "unknown"),
            }
        )
    return out


def role_rows(run_dirs: list[Path]) -> pd.DataFrame:
    """All actors of the finished episodes; a crashed or interrupted one has no `trace.json`."""
    rows = [
        row
        for run in run_dirs
        for episode in sorted(p.parent for p in run.glob("*/trace.json"))
        for row in episode_roles(episode)
    ]
    return pd.DataFrame(rows)


def parity_table(df: pd.DataFrame) -> pd.DataFrame:
    """Per configured model and role: is what the roles got the same across the sample?"""
    g = df.groupby(["configured_model", "role"])
    return pd.DataFrame(
        {
            "actors": g.size(),
            "served_models": g["served_model"].agg(lambda s: " ; ".join(sorted(set(s)))),
            "providers": g["served_provider"].agg(lambda s: " ; ".join(sorted(set(s)))),
            "system_prompts": g["system_prompt"].nunique(),
            "tool_sets": g["tools"].agg(lambda s: " ; ".join(sorted(set(s)))),
            "stand_rules": g["stand_rules"].mean(),
            "asks_once": g["asks_once"].mean(),
            "task_chars": g["task_chars"].mean(),
            "steps_mean": g["steps"].mean(),
            "hit_max_steps": g["stop_reason"].agg(lambda s: (s == "max_steps").mean()),
        }
    )


def served_mismatches(df: pd.DataFrame) -> pd.DataFrame:
    """Episodes where roles configured with the same model were served by different models or
    providers: a role difference the config does not show."""
    agents = df[df["role"].isin(["orchestrator", "subagent"])]
    per = agents.groupby(["run", "episode_id", "configured_model"]).agg(
        models=("served_model", "nunique"),
        providers=("served_provider", "nunique"),
    )
    return per[(per["models"] > 1) | (per["providers"] > 1)]
