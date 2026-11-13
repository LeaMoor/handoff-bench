import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import ORCH_TOOLS_CALIBRATION, ORCH_TOOLS_MAIN, AgentsConfig
from handoff_bench.agents.loop import ModelCallLog
from handoff_bench.agents.model_client import ModelClient, ModelSettings
from handoff_bench.billing import EffectJournal
from handoff_bench.cells import CELLS, Cell, UnsupportedCell
from handoff_bench.channels.render import RENDERERS
from handoff_bench.faults.setup import FAULTS
from handoff_bench.grading.coder import code_behaviour
from handoff_bench.grading.grader import grade
from handoff_bench.jsonl import read_rows
from handoff_bench.runner.episode import ORCHESTRATOR, EpisodeConfig, run_episode
from handoff_bench.runner.results import ResultRow
from handoff_bench.worlds import generate


class EpisodeSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: int
    world: int = 1
    template: int
    seed: int
    repeat: int = 0

    @property
    def episode_id(self) -> str:
        return f"c{self.cell:02d}-w{self.world}-t{self.template}-s{self.seed}-r{self.repeat}"


def check_supported(cell: Cell) -> None:
    if cell.fault not in FAULTS or cell.channel not in RENDERERS:
        raise UnsupportedCell(
            f"cell {cell.number} (fault={cell.fault}, channel={cell.channel}) is not wired yet"
        )


def _served(log_path: Path) -> tuple[str, str]:
    responses = [r.response or {} for r in read_rows(log_path, ModelCallLog)]

    def joined(field: str) -> str:
        return "|".join(sorted({str(r[field]) for r in responses if r.get(field)}))

    return joined("model"), joined("provider")


async def run_one(
    spec: EpisodeSpec,
    model: ModelClient,
    *,
    settings: ModelSettings,
    run_dir: Path,
    run_id: str,
    commit: str,
) -> ResultRow:
    cell = CELLS[spec.cell]
    check_supported(cell)
    world = generate(spec.world, spec.seed, spec.template)
    episode_dir = run_dir / spec.episode_id
    tools = ORCH_TOOLS_MAIN if cell.block == "main" else ORCH_TOOLS_CALIBRATION
    config = EpisodeConfig(
        episode_id=spec.episode_id,
        cell=cell.number,
        fault=cell.fault,
        channel=cell.channel,
        world=spec.world,
        template=spec.template,
        seed=spec.seed,
        repeat=spec.repeat,
        agents=AgentsConfig(orchestrator_tools=tools),
        model=settings,
        commit=commit,
    )
    result = await run_episode(model, world, config, episode_dir=episode_dir)
    g = grade(
        EffectJournal.read(episode_dir / "effects.jsonl"),
        focal_invoice=world.focal_invoice,
        targets=world.targets,
        finish_args=result.finish_args,
        stop_reason=result.stop_reason,
    )
    b = code_behaviour(
        CallJournal.read(episode_dir / "calls.jsonl"),
        orchestrator=ORCHESTRATOR,
        final_text=result.final_text,
    )
    (episode_dir / "grade.json").write_text(
        json.dumps({"grade": g.model_dump(), "behaviour": b.model_dump()}, indent=2),
        encoding="utf-8",
    )
    served_model, served_provider = _served(episode_dir / "model_calls.jsonl")
    return ResultRow(
        run_id=run_id,
        episode_id=spec.episode_id,
        cell=cell.number,
        block=cell.block,
        fault=cell.fault,
        channel=cell.channel,
        world=spec.world,
        template=spec.template,
        seed=spec.seed,
        repeat=spec.repeat,
        phrasing=world.phrasing,
        model=settings.model,
        provider=settings.provider,
        served_model=served_model,
        served_provider=served_provider,
        **g.model_dump(),
        **b.model_dump(),
        delegations=result.delegations,
        stop_reason=result.stop_reason,
        steps=result.steps,
        retries=result.retries,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        reasoning_tokens=result.reasoning_tokens,
        cached_tokens=result.cached_tokens,
        cost=result.cost,
        commit=commit,
        error=result.error,
    )
