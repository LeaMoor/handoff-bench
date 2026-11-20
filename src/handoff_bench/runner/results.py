"""`results.csv`: one row per episode, the only input of the analysis."""

import csv
import subprocess
from pathlib import Path

from pydantic import BaseModel

from handoff_bench.agents.tools import StopReason
from handoff_bench.cells import Agent, Block, Channel, Delay, Fault, KeySource
from handoff_bench.grading.coder import Action
from handoff_bench.grading.grader import Claim, Effect, Outcome


class ResultRow(BaseModel):
    run_id: str
    episode_id: str
    cell: int
    block: Block
    fault: Fault
    channel: Channel
    delay: Delay
    rules: bool
    agent: Agent
    key: KeySource
    world: int
    template: int
    seed: int
    repeat: int
    phrasing: int
    model: str
    provider: str
    served_model: str
    served_provider: str
    fault_fired: bool
    outcome: Outcome
    correct: bool | None
    effect: Effect
    claim: Claim
    honest_unknown: bool
    false_done: bool
    focal_charges: int
    focal_amount_ok: bool
    other_target_errors: int
    stray_charges: int
    first_action: Action
    decision: Action
    self_checked: bool
    delegations: int
    stop_reason: StopReason
    steps: int
    retries: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    cost: float
    commit: str
    error: str | None


def git_commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        sha, dirty = git("rev-parse", "HEAD"), git("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


class ResultsCsv:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fields = list(ResultRow.model_fields)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, self.fields).writeheader()

    def append(self, row: ResultRow) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, self.fields).writerow(row.model_dump())
