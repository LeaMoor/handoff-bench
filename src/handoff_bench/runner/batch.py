import traceback
from collections.abc import Callable
from pathlib import Path

import anyio
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from handoff_bench.agents.loop import ModelCallLog
from handoff_bench.agents.model_client import ModelClient, ModelSettings
from handoff_bench.cells import CELLS
from handoff_bench.jsonl import read_rows
from handoff_bench.runner.episode import EpisodeConfig
from handoff_bench.runner.results import ResultsCsv
from handoff_bench.runner.run import check_supported, run_one
from handoff_bench.worlds import TEMPLATES, WORLDS
from handoff_bench.worlds.generator import OWNERSHIP_TEMPLATE


class BatchConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str | None = None
    model: ModelSettings
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
            check_supported(CELLS[number])
            if CELLS[number].ownership is not None and self.templates != [OWNERSHIP_TEMPLATE]:
                raise ValueError(f"M8 requires templates: [{OWNERSHIP_TEMPLATE}]")
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


class BatchSummary(BaseModel):
    run_dir: Path
    finished: int
    crashed: int
    skipped: int
    cost: float
    cost_missing: bool


async def run_batch(
    config: BatchConfig,
    make_model: Callable[[EpisodeConfig], ModelClient],
    *,
    run_dir: Path,
    run_id: str,
    commit: str,
) -> BatchSummary:
    """Running episodes finish after the budget is spent: overshoot is up to `parallel` episodes.
    Tokens without a reported cost also stop the batch, or the budget would not hold."""
    if commit.endswith("-dirty") and not config.allow_dirty:
        raise DirtyCheckout("uncommitted changes: commit them or set allow_dirty for a debug run")
    results = ResultsCsv(run_dir / "results.csv")
    limiter = anyio.CapacityLimiter(config.parallel)
    finished, crashed, skipped, spent, cost_missing = 0, 0, 0, 0.0, False

    async def one(episode: EpisodeConfig) -> None:
        nonlocal finished, crashed, skipped, spent, cost_missing
        async with limiter:
            if cost_missing or spent >= config.budget_usd:
                skipped += 1
                return
            try:
                row = await run_one(episode, make_model(episode), run_dir=run_dir, run_id=run_id)
            except Exception:
                crashed += 1
                episode_dir = run_dir / episode.episode_id
                episode_dir.mkdir(parents=True, exist_ok=True)
                (episode_dir / "crash.txt").write_text(traceback.format_exc(), encoding="utf-8")
                spent += _spent_before_crash(episode_dir)
                return
            spent += row.cost
            cost_missing = cost_missing or (row.cost == 0 and row.prompt_tokens > 0)
            finished += 1
            results.append(row)

    async with anyio.create_task_group() as tg:
        for episode in config.episodes(commit):
            tg.start_soon(one, episode)
    return BatchSummary(
        run_dir=run_dir,
        finished=finished,
        crashed=crashed,
        skipped=skipped,
        cost=spent,
        cost_missing=cost_missing,
    )
