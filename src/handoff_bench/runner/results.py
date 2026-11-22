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
    orchestrator: str = "stand"  # our loop, or a harness: `claude_code:opus`
    served_model: str
    served_provider: str
    fault_fired: bool
    outcome: Outcome
    correct: bool | None
    effect: Effect
    claim: Claim
    sub_claim: Claim = "none"
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


_PACKAGE_DIR = Path(__file__).resolve().parent


def git_commit(repo: Path = _PACKAGE_DIR) -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        # Untracked files (local configs, .DS_Store) do not change the code that runs.
        sha = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


class ResultsCsv:
    def __init__(self, path: Path, *, resume: bool = False) -> None:
        """A new file; with `resume`, the existing one keeps its valid rows (`kept`) and drops
        the invalid ones, so their episodes run again."""
        self.path = path
        self.fields = list(ResultRow.model_fields)
        self.kept: list[dict[str, str]] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        if resume and path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                self.kept = [r for r in csv.DictReader(f) if r.get("outcome") != "invalid"]
        with path.open("w" if resume else "x", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, self.fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.kept)

    def append(self, row: ResultRow) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, self.fields).writerow(row.model_dump())
