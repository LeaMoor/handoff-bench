"""One episode: the orchestrator delegates the task of a generated world to a subagent."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import AgentsConfig, Delegation, register_finish
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, ModelCallLog, StopReason, run_agent
from handoff_bench.agents.mcp_backend import McpBackend
from handoff_bench.agents.model_client import ModelClient, ModelSettings, Usage
from handoff_bench.agents.tools import INVALID_STOPS
from handoff_bench.billing import BillingService, EffectJournal
from handoff_bench.billing.server import running_server
from handoff_bench.cells import Channel, Fault
from handoff_bench.channels.render import RENDERERS
from handoff_bench.faults.setup import FAULTS
from handoff_bench.jsonl import JsonlWriter
from handoff_bench.worlds import World

ORCHESTRATOR = "orchestrator"


class EpisodeConfig(BaseModel):
    """Everything that sets an episode apart; written to `config.json` next to its logs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    episode_id: str
    cell: int
    fault: Fault = "none"
    channel: Channel = "text"
    world: int
    template: int
    seed: int
    repeat: int = 0
    agents: AgentsConfig = AgentsConfig()
    model: ModelSettings | None = None
    commit: str = "unknown"


class EpisodeResult(BaseModel):
    episode_dir: Path
    stop_reason: StopReason
    steps: int
    delegations: int
    fault: Fault
    fault_fired: bool
    finish_args: dict[str, Any] | None
    final_text: str | None
    cost: float
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    retries: int
    error: str | None


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def run_episode(
    model: ModelClient, world: World, config: EpisodeConfig, *, episode_dir: Path
) -> EpisodeResult:
    """Writes config, calls, effects, model_calls, trace and summary to episode_dir."""
    fault = FAULTS[config.fault](world)
    agents = config.agents
    episode_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        episode_dir / "config.json",
        {"episode": config.model_dump(mode="json"), "bank": fault.bank.model_dump(mode="json")},
    )
    service = BillingService(
        episode_id=config.episode_id,
        config=fault.bank,
        journal=EffectJournal(episode_dir / "effects.jsonl"),
        db_path=episode_dir / "bank.sqlite",
        id_seed=config.seed,
    )
    service.add_invoices(world.invoices)

    with service, running_server(service) as url:
        async with McpBackend(url) as bank:
            gateway = ToolGateway(
                episode_id=config.episode_id,
                journal=CallJournal(episode_dir / "calls.jsonl"),
                backends=[bank],
                hooks=[fault.injector],
            )
            log = JsonlWriter[ModelCallLog](episode_dir / "model_calls.jsonl")
            delegation = Delegation(
                gateway,
                model=model,
                log=log,
                report_lost=fault.injector.report_lost,
                config=agents,
                render=RENDERERS[config.channel],
            )
            register_finish(gateway)
            spec = AgentSpec(
                actor=ORCHESTRATOR,
                system_prompt=agents.orchestrator_prompt,
                max_steps=agents.orchestrator_max_steps,
                tools=gateway.openai_tools(list(agents.orchestrator_tools)),
                finish_tools=frozenset({"finish"}),
            )
            trace = await run_agent(spec, world.task, model=model, gateway=gateway, log=log)

    subagents = delegation.subagents
    _write_json(
        episode_dir / "trace.json",
        {
            "orchestrator": trace.model_dump(mode="json"),
            "subagents": [s.model_dump(mode="json") for s in subagents],
        },
    )
    traces = [trace, *subagents]
    usage = sum((t.usage for t in traces), Usage())
    # A subagent's API / stand failure invalidates the whole episode.
    broken = next((s for s in subagents if s.stop_reason in INVALID_STOPS), None)
    result = EpisodeResult(
        episode_dir=episode_dir,
        stop_reason=broken.stop_reason if broken else trace.stop_reason,
        steps=trace.steps,
        delegations=len(subagents),
        fault=config.fault,
        fault_fired=fault.injector.fired,
        finish_args=trace.finish_args,
        final_text=trace.messages[-1].get("content") if trace.stop_reason == "text_only" else None,
        cost=usage.cost,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cached_tokens=usage.cached_tokens,
        retries=sum(t.retries for t in traces),
        error=broken.error if broken else trace.error,
    )
    _write_json(episode_dir / "summary.json", result.model_dump(mode="json"))
    return result
