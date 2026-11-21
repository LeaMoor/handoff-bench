"""One episode: the orchestrator delegates the task of a generated world to a subagent."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from handoff_bench.agents.call_journal import CallJournal, CallRow, is_processing
from handoff_bench.agents.delegation import (
    ORCH_TOOLS_CALIBRATION,
    ORCH_TOOLS_MAIN,
    ORCHESTRATOR_SYSTEM_PROMPT,
    SINGLE_SYSTEM_PROMPT,
    SINGLE_TOOLS,
    AgentsConfig,
    Delegation,
    register_finish,
)
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, AgentTrace, ModelCallLog, StopReason, run_agent
from handoff_bench.agents.mcp_backend import McpBackend
from handoff_bench.agents.model_client import ModelClient, ModelSettings, Usage
from handoff_bench.agents.tools import INVALID_STOPS
from handoff_bench.billing import BillingService, EffectJournal
from handoff_bench.billing.server import running_server
from handoff_bench.cells import CELLS, Agent, Block, Cell, Channel, Delay, Fault, KeySource
from handoff_bench.channels.render import RENDERERS
from handoff_bench.faults.setup import FAULTS, with_delay
from handoff_bench.grading.grader import Claim
from handoff_bench.jsonl import JsonlWriter
from handoff_bench.keys import ORCHESTRATOR_KEY_PROMPT, KeyHook
from handoff_bench.worlds import World

ORCHESTRATOR = "orchestrator"


class EpisodeConfig(BaseModel):
    """Everything that sets an episode apart; written to `config.json` next to its logs.
    Fault, channel, delay, rules, agent and the orchestrator's tools follow from the cell."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: int
    world: int = 1
    template: int
    seed: int
    repeat: int = 0
    agents: AgentsConfig
    model: ModelSettings | None = None
    commit: str = "unknown"

    @model_validator(mode="before")
    @classmethod
    def _from_cell(cls, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("cell") not in CELLS:
            raise ValueError(f"unknown cell {data.get('cell')}")
        cell = CELLS[data["cell"]]
        # A config.json read back carries the derived fields: they must agree with the cell.
        data = dict(data)
        for name in ("block", "fault", "channel", "delay", "rules", "agent", "key"):
            if name in data and data.pop(name) != getattr(cell, name):
                raise ValueError(f"{name} does not match cell {cell.number}")
        data.pop("episode_id", None)
        if "agents" not in data:
            data["agents"] = _agents_for(cell)
        return data

    @computed_field
    @property
    def episode_id(self) -> str:
        return f"c{self.cell:02d}-w{self.world}-t{self.template}-s{self.seed}-r{self.repeat}"

    @computed_field
    @property
    def block(self) -> Block:
        return CELLS[self.cell].block

    @computed_field
    @property
    def fault(self) -> Fault:
        return CELLS[self.cell].fault

    @computed_field
    @property
    def channel(self) -> Channel:
        return CELLS[self.cell].channel

    @computed_field
    @property
    def delay(self) -> Delay:
        return CELLS[self.cell].delay

    @computed_field
    @property
    def rules(self) -> bool:
        return CELLS[self.cell].rules

    @computed_field
    @property
    def agent(self) -> Agent:
        return CELLS[self.cell].agent

    @computed_field
    @property
    def key(self) -> KeySource:
        return CELLS[self.cell].key


def _agents_for(cell: Cell) -> AgentsConfig:
    if cell.agent == "single":
        return AgentsConfig(
            orchestrator_tools=SINGLE_TOOLS, orchestrator_prompt=SINGLE_SYSTEM_PROMPT
        )
    tools = ORCH_TOOLS_MAIN if cell.block in ("main", "processing") else ORCH_TOOLS_CALIBRATION
    extra: dict[str, Any] = {}
    if not cell.rules:
        extra["subagent_rules"] = ""
    if cell.key == "orchestrator":
        extra["orchestrator_prompt"] = ORCHESTRATOR_SYSTEM_PROMPT + ORCHESTRATOR_KEY_PROMPT
    return AgentsConfig(orchestrator_tools=tools, **extra)


class EpisodeResult(BaseModel):
    episode_dir: Path
    stop_reason: StopReason
    steps: int
    delegations: int
    orchestrator_responses: list[int]
    fault_fired: bool
    finish_args: dict[str, Any] | None
    sub_claim: Claim = "none"  # what the subagent the fault hit reported
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
    """Writes config, calls, effects, model_calls and trace to episode_dir."""
    fault = with_delay(FAULTS[config.fault](world), config.delay, world)
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

    tasks: dict[str, str] = {}
    keys = KeyHook(config.key, tasks)
    with service, running_server(service) as url:
        async with McpBackend(url) as bank:
            gateway = ToolGateway(
                episode_id=config.episode_id,
                journal=CallJournal(episode_dir / "calls.jsonl"),
                backends=[bank],
                hooks=[keys, fault.injector],
            )
            log = JsonlWriter[ModelCallLog](episode_dir / "model_calls.jsonl")
            delegation = Delegation(
                gateway,
                model=model,
                log=log,
                report_lost=fault.injector.report_lost,
                config=agents,
                render=RENDERERS[config.channel],
                deliveries=fault.deliveries,
                respawn_on_lost=fault.respawn_on_lost,
                tasks=tasks,
                hide_key=config.key == "none",
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
    return EpisodeResult(
        episode_dir=episode_dir,
        stop_reason=broken.stop_reason if broken else trace.stop_reason,
        steps=trace.steps,
        delegations=len(subagents),
        orchestrator_responses=[
            len(m["tool_calls"]) for m in trace.messages if m.get("tool_calls")
        ],
        # Bank faults fired if a call came back as a timeout or as a processing charge.
        fault_fired=fault.injector.fired or any(_bank_fault(r) for r in gateway.journal.rows),
        finish_args=trace.finish_args,
        sub_claim=_faulted_claim(subagents, gateway.journal.rows, fault.injector.fired_on),
        final_text=trace.messages[-1].get("content") if trace.stop_reason == "text_only" else None,
        cost=usage.cost,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cached_tokens=usage.cached_tokens,
        retries=sum(t.retries for t in traces),
        error=broken.error if broken else trace.error,
    )


def _bank_fault(r: CallRow) -> bool:
    return r.outcome == "timeout" or is_processing(r)


def _faulted_claim(subagents: list[AgentTrace], rows: list[CallRow], fired_on: str | None) -> Claim:
    """What the subagent the fault hit reported: where a false claim was born."""
    actor = fired_on or next(
        (r.actor for r in sorted(rows, key=lambda r: r.seq) if _bank_fault(r)), None
    )
    child = next((s for s in subagents if s.actor == actor), None)
    if child is None or child.finish_tool != "finish_report" or child.finish_args is None:
        return "none"
    return child.finish_args["status"]
