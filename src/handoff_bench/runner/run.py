import json
from pathlib import Path

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.loop import ModelCallLog
from handoff_bench.agents.model_client import ModelClient
from handoff_bench.billing import EffectJournal
from handoff_bench.cells import Cell, UnsupportedCell
from handoff_bench.channels.render import RENDERERS
from handoff_bench.faults.setup import FAULTS
from handoff_bench.grading.coder import code_behaviour
from handoff_bench.grading.grader import grade
from handoff_bench.harness.claude_code import ClaudeCodeOrchestrator
from handoff_bench.harness.codex_native import run_codex_native_episode
from handoff_bench.harness.hermes import HermesOrchestrator, HermesSettings
from handoff_bench.harness.hermes_native import run_hermes_native_episode
from handoff_bench.harness.native import run_native_episode
from handoff_bench.jsonl import read_rows
from handoff_bench.runner.episode import ORCHESTRATOR, EpisodeConfig, EpisodeResult, run_episode
from handoff_bench.runner.results import ResultRow
from handoff_bench.worlds import World, generate


def check_supported(cell: Cell) -> None:
    # The harness's own subagents use no stand renderer.
    if cell.block in ("native", "hermes_native", "codex_native"):
        return
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
    config: EpisodeConfig, model: ModelClient | None, *, run_dir: Path, run_id: str
) -> ResultRow:
    """Episode → grade → `results.csv` row, also saved as `result.json` next to the logs."""
    world = generate(config.world, config.seed, config.template)
    episode_dir = run_dir / config.episode_id
    if config.block == "native":
        result = await run_native_episode(world, config, episode_dir=episode_dir)
    elif config.block == "hermes_native":
        result = await run_hermes_native_episode(world, config, episode_dir=episode_dir)
    elif config.block == "codex_native":
        result = await run_codex_native_episode(world, config, episode_dir=episode_dir)
    else:
        if model is None:
            raise ValueError("A model client is required for this cell")
        if isinstance(config.orchestrator, HermesSettings):
            harness = HermesOrchestrator(config.orchestrator)
        elif config.orchestrator:
            harness = ClaudeCodeOrchestrator(config.orchestrator)
        else:
            harness = None
        result = await run_episode(
            model, world, config, episode_dir=episode_dir, orchestrator=harness
        )
    return record_episode(config, world, result, run_id=run_id)


def record_episode(
    config: EpisodeConfig, world: World, result: EpisodeResult, *, run_id: str
) -> ResultRow:
    episode_dir = result.episode_dir
    g = grade(
        EffectJournal.read(episode_dir / "effects.jsonl"),
        world=world,
        finish_args=result.finish_args,
        stop_reason=result.stop_reason,
    )
    b = code_behaviour(
        CallJournal.read(episode_dir / "calls.jsonl"),
        orchestrator=ORCHESTRATOR,
        responses=result.orchestrator_responses,
        final_text=result.final_text,
    )
    served_model, served_provider = _served(episode_dir / "model_calls.jsonl")
    row = ResultRow(
        run_id=run_id,
        episode_id=config.episode_id,
        cell=config.cell,
        block=config.block,
        fault=config.fault,
        channel=config.channel,
        delay=config.delay,
        rules=config.rules,
        agent=config.agent,
        key=config.key,
        world=config.world,
        template=config.template,
        seed=config.seed,
        repeat=config.repeat,
        phrasing=world.phrasing,
        model=config.model.model if config.model else "unknown",
        provider=config.model.provider if config.model else "unknown",
        orchestrator=config.orchestrator.label if config.orchestrator else "stand",
        served_model=served_model,
        served_provider=served_provider,
        fault_fired=result.fault_fired,
        sub_claim=result.sub_claim,
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
        commit=config.commit,
        error=result.error,
    )
    (episode_dir / "result.json").write_text(
        json.dumps(row.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return row
