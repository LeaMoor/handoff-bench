import shutil
import traceback
from collections.abc import Callable
from pathlib import Path

import anyio
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from handoff_bench.agents.loop import ModelCallLog
from handoff_bench.agents.model_client import ModelClient, ModelSettings
from handoff_bench.cells import CELLS
from handoff_bench.harness.claude_code import HarnessSettings, UsageLimitReached
from handoff_bench.harness.codex_native import CodexSettings
from handoff_bench.harness.hermes import HermesSettings
from handoff_bench.jsonl import read_rows
from handoff_bench.runner.episode import EpisodeConfig
from handoff_bench.runner.results import ResultsCsv
from handoff_bench.runner.run import check_supported, run_one
from handoff_bench.worlds import TEMPLATES, WORLDS


class BatchConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str | None = None
    model: ModelSettings
    # None runs our loop on `model`.
    orchestrator: HarnessSettings | HermesSettings | CodexSettings | None = None
    cells: list[int]
    world: int = 1
    templates: list[int]
    seeds: list[int]
    repeats: int = Field(default=1, ge=1)
    parallel: int = Field(default=4, ge=1, le=8)
    budget_usd: float = Field(gt=0)
    allow_dirty: bool = False

    @model_validator(mode="after")
    def _check(self) -> "BatchConfig":
        for name in ("cells", "templates", "seeds"):
            values = getattr(self, name)
            if not values or len(set(values)) != len(values):
                raise ValueError(f"{name} must be non-empty and without repeats")
        if self.world not in WORLDS:
            raise ValueError(f"unknown world {self.world}")
        if unknown := set(self.templates) - set(TEMPLATES):
            raise ValueError(f"unknown templates {sorted(unknown)}")
        for number in self.cells:
            if number not in CELLS:
                raise ValueError(f"unknown cell {number}")
            cell = CELLS[number]
            if (cell.block == "codex_native") != isinstance(self.orchestrator, CodexSettings):
                raise ValueError("Codex native cells require Codex and a separate batch")
            check_supported(cell)
        if isinstance(self.orchestrator, CodexSettings) and self.parallel != 1:
            raise ValueError("Codex batches require parallel: 1")
        if isinstance(self.orchestrator, HermesSettings) and self.parallel != 1:
            raise ValueError("Hermes batches currently require parallel: 1")
        return self

    @classmethod
    def load(cls, path: Path) -> "BatchConfig":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def episodes(self, commit: str) -> list[EpisodeConfig]:
        return [
            EpisodeConfig(
                cell=c,
                world=self.world,
                template=t,
                seed=s,
                repeat=r,
                model=self.model,
                orchestrator=self.orchestrator,
                commit=commit,
            )
            for t in self.templates
            for s in self.seeds
            for r in range(self.repeats)
            for c in self.cells
        ]


class DirtyCheckout(RuntimeError):
    pass


def _spent_before_crash(episode_dir: Path) -> float:
    log = episode_dir / "model_calls.jsonl"
    return sum(r.usage.cost for r in read_rows(log, ModelCallLog)) if log.exists() else 0.0


def _usage_limit(e: BaseException) -> UsageLimitReached | None:
    """The limit error, also when the episode's task groups wrapped it."""
    if isinstance(e, UsageLimitReached):
        return e
    if isinstance(e, BaseExceptionGroup):
        return next((h for x in e.exceptions if (h := _usage_limit(x))), None)
    return None


def _write_crash(episode_dir: Path) -> None:
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / "crash.txt").write_text(traceback.format_exc(), encoding="utf-8")


class BatchSummary(BaseModel):
    run_dir: Path
    finished: int
    crashed: int
    skipped: int
    cost: float
    cost_missing: bool
    done_before: int = 0  # valid rows a resumed batch kept
    limit_reached: str | None = None  # the harness's usage limit stopped the batch


async def run_batch(
    config: BatchConfig,
    make_model: Callable[[EpisodeConfig], ModelClient | None],
    *,
    run_dir: Path,
    run_id: str,
    commit: str,
    resume: bool = False,
) -> BatchSummary:
    """Running episodes finish after the budget is spent: overshoot is up to `parallel` episodes.
    Tokens without a reported cost also stop the batch, or the budget would not hold.

    The harness's usage limit stops the batch: the episodes it cut are discarded, the rest are
    skipped, and `resume` runs both later (episodes with a valid row are kept, their cost counts
    towards the budget)."""
    if commit.endswith("-dirty") and not config.allow_dirty:
        raise DirtyCheckout("uncommitted changes: commit them or set allow_dirty for a debug run")
    results = ResultsCsv(run_dir / "results.csv", resume=resume)
    done = {r["episode_id"] for r in results.kept}
    episodes = [e for e in config.episodes(commit) if e.episode_id not in done]
    for episode in episodes:  # logs of an unfinished run would mix with the new ones
        shutil.rmtree(run_dir / episode.episode_id, ignore_errors=True)
    limiter = anyio.CapacityLimiter(config.parallel)
    finished, crashed, skipped, cost_missing = 0, 0, 0, False
    spent = sum(float(r["cost"] or 0) for r in results.kept)
    limit: UsageLimitReached | None = None
    codex = isinstance(config.orchestrator, CodexSettings)
    codex_failed = False

    async def one(episode: EpisodeConfig) -> None:
        nonlocal finished, crashed, skipped, spent, cost_missing, limit, codex_failed
        async with limiter:
            if limit is not None or cost_missing or codex_failed or spent >= config.budget_usd:
                skipped += 1
                return
            try:
                row = await run_one(episode, make_model(episode), run_dir=run_dir, run_id=run_id)
            except Exception as e:
                if (hit := _usage_limit(e)) is None:
                    crashed += 1
                    codex_failed = codex
                    _write_crash(run_dir / episode.episode_id)
                    spent += _spent_before_crash(run_dir / episode.episode_id)
                    return
                limit = limit or hit
                skipped += 1
                if not codex:
                    shutil.rmtree(run_dir / episode.episode_id, ignore_errors=True)
                return
            spent += row.cost
            # Codex reports subscription token usage without a billed dollar amount.
            if codex:
                codex_failed = row.outcome == "invalid"
            else:
                cost_missing = cost_missing or (row.cost == 0 and row.prompt_tokens > 0)
            finished += 1
            results.append(row)

    async with anyio.create_task_group() as tg:
        for episode in episodes:
            tg.start_soon(one, episode)
    return BatchSummary(
        run_dir=run_dir,
        finished=finished,
        crashed=crashed,
        skipped=skipped,
        cost=spent,
        cost_missing=cost_missing,
        done_before=len(done),
        limit_reached=str(limit) if limit else None,
    )
