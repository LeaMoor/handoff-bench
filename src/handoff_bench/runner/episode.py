"""One episode: the orchestrator delegates paying the invoice to a subagent.

Until the world generator (task 5) lands, the bank holds the demo invoices."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import (
    ORCH_MAX_STEPS,
    ORCH_TOOLS_CALIBRATION,
    ORCHESTRATOR_SYSTEM_PROMPT,
    Delegation,
    register_finish,
)
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, ModelCallLog, StopReason, run_agent
from handoff_bench.agents.mcp_backend import McpBackend
from handoff_bench.agents.model_client import ModelClient, Usage
from handoff_bench.billing import BillingService, EffectJournal
from handoff_bench.billing.demo import DEMO_INVOICES
from handoff_bench.billing.server import running_server
from handoff_bench.jsonl import JsonlWriter

ORCHESTRATOR = "orchestrator"
FOCAL_INVOICE = "INV-12"
TASK = f"Pay invoice {FOCAL_INVOICE} of customer Ann (cus_ann) and report the result to me."
INVALID_STOPS = ("api_error", "infra_error")


class EpisodeResult(BaseModel):
    episode_dir: Path
    stop_reason: StopReason
    steps: int
    delegations: int
    finish_args: dict[str, Any] | None
    cost: float
    prompt_tokens: int
    completion_tokens: int
    retries: int
    error: str | None


async def run_episode(
    model: ModelClient,
    *,
    episode_dir: Path,
    episode_id: str,
    seed: int = 0,
    orchestrator_tools: tuple[str, ...] = ORCH_TOOLS_CALIBRATION,
) -> EpisodeResult:
    """Run one episode and write calls, effects, model_calls, trace and summary to episode_dir."""
    service = BillingService(
        episode_id=episode_id,
        journal=EffectJournal(episode_dir / "effects.jsonl"),
        db_path=episode_dir / "bank.sqlite",
        id_seed=seed,
    )
    service.add_invoices(DEMO_INVOICES)

    with service, running_server(service) as url:
        async with McpBackend(url) as bank:
            gateway = ToolGateway(
                episode_id=episode_id,
                journal=CallJournal(episode_dir / "calls.jsonl"),
                backends=[bank],
            )
            log = JsonlWriter[ModelCallLog](episode_dir / "model_calls.jsonl")
            delegation = Delegation(gateway, model=model, log=log)
            register_finish(gateway)
            spec = AgentSpec(
                actor=ORCHESTRATOR,
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                max_steps=ORCH_MAX_STEPS,
                tools=gateway.openai_tools(list(orchestrator_tools)),
                finish_tools=frozenset({"finish"}),
            )
            trace = await run_agent(spec, TASK, model=model, gateway=gateway, log=log)

    subagents = delegation.subagents
    (episode_dir / "trace.json").write_text(
        json.dumps(
            {
                "orchestrator": trace.model_dump(mode="json"),
                "subagents": [s.model_dump(mode="json") for s in subagents],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
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
        finish_args=trace.finish_args,
        cost=usage.cost,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        retries=sum(t.retries for t in traces),
        error=broken.error if broken else trace.error,
    )
    (episode_dir / "summary.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
