import traceback
from collections.abc import Callable
from pathlib import Path

import anyio
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from handoff_bench.agents.model_client import ModelClient, ModelSettings
from handoff_bench.cells import CELLS
from handoff_bench.runner.results import ResultsCsv
from handoff_bench.runner.run import EpisodeSpec, check_supported, run_one
from handoff_bench.worlds import TEMPLATES, WORLDS


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
        return self

    @classmethod
    def load(cls, path: Path) -> "BatchConfig":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def episodes(self) -> list[EpisodeSpec]:
        return [
            EpisodeSpec(cell=c, world=self.world, template=t, seed=s, repeat=r)
            for c in self.cells
            for t in self.templates
            for s in self.seeds
            for r in range(self.repeats)
        ]


class BatchSummary(BaseModel):
    run_dir: Path
    finished: int
    crashed: int
    skipped: int
    cost: float
    cost_missing: bool


async def run_batch(
    config: BatchConfig,
    make_model: Callable[[EpisodeSpec], ModelClient],
    *,
    run_dir: Path,
    run_id: str,
    commit: str,
) -> BatchSummary:
    """Running episodes finish after the budget is spent: overshoot is up to `parallel` episodes.
    Tokens without a reported cost also stop the batch, or the budget would not hold."""
    results = ResultsCsv(run_dir / "results.csv")
    limiter = anyio.CapacityLimiter(config.parallel)
    finished, crashed, skipped, spent, cost_missing = 0, 0, 0, 0.0, False

    async def one(spec: EpisodeSpec) -> None:
        nonlocal finished, crashed, skipped, spent, cost_missing
        async with limiter:
            if cost_missing or spent >= config.budget_usd:
                skipped += 1
                return
            try:
                row = await run_one(
                    spec,
                    make_model(spec),
                    settings=config.model,
                    run_dir=run_dir,
                    run_id=run_id,
                    commit=commit,
                )
            except Exception:
                crashed += 1
                episode_dir = run_dir / spec.episode_id
                episode_dir.mkdir(parents=True, exist_ok=True)
                (episode_dir / "crash.txt").write_text(traceback.format_exc(), encoding="utf-8")
                return
            spent += row.cost
            cost_missing = cost_missing or (row.cost == 0 and row.prompt_tokens > 0)
            finished += 1
            results.append(row)

    async with anyio.create_task_group() as tg:
        for spec in config.episodes():
            tg.start_soon(one, spec)
    return BatchSummary(
        run_dir=run_dir,
        finished=finished,
        crashed=crashed,
        skipped=skipped,
        cost=spent,
        cost_missing=cost_missing,
    )
