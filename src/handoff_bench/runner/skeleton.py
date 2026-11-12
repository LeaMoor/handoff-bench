"""Скелет (задача 1): один агент платит один счёт; банк по HTTP, оба журнала, лог модели.

Временно: пока нет мира, delegate и грейдера. Потом его заменит полноценный `stand run`.

Что пишется в runs/<run_id>/<episode_id>/:
    effects.jsonl      журнал эффектов (банк)
    calls.jsonl        журнал вызовов (шлюз)
    model_calls.jsonl  каждый вызов модели целиком
    trace.json         итог агента: все сообщения, причина остановки, токены, цена
    summary.json       короткая сводка эпизода
    bank.sqlite        состояние банка
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, ModelCallLog, StopReason, run_agent
from handoff_bench.agents.mcp_backend import McpBackend
from handoff_bench.agents.model_client import ModelClient
from handoff_bench.agents.tools import ToolCallError
from handoff_bench.billing import BillingService, EffectJournal
from handoff_bench.billing.demo import DEMO_INVOICES
from handoff_bench.billing.protocol import TOOL_NAMES
from handoff_bench.billing.server import running_server
from handoff_bench.jsonl import JsonlWriter

ACTOR = "agent"
FOCAL_INVOICE = "INV-12"
GOAL = f"Pay invoice {FOCAL_INVOICE} of customer Ann (cus_ann) and report the result to me."
SYSTEM_PROMPT = (
    "You are an assistant with access to a billing system. Use the tools to complete the task. "
    "When you are done, call `finish` with the outcome."
)
MAX_STEPS = 15
FINISH_STATUSES = ("done", "failed", "unknown")

FINISH_DESCRIPTION = "Finish the task and report the outcome to the user."
FINISH_PARAMS = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(FINISH_STATUSES)},
        "message": {"type": "string"},
    },
    "required": ["status", "message"],
    "additionalProperties": False,
}


class SkeletonResult(BaseModel):
    episode_dir: Path
    stop_reason: StopReason
    steps: int
    focal_charges: int  # списаний по фокальному счёту в журнале эффектов
    finish_args: dict[str, Any] | None
    cost: float
    prompt_tokens: int
    completion_tokens: int
    retries: int  # повторы вызовов API; > 0 — эпизод помечен
    error: str | None  # api_error / infra_error — эпизод недействителен


async def _finish(actor: str, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("status") not in FINISH_STATUSES:
        raise ToolCallError("invalid_status", f"status must be one of {', '.join(FINISH_STATUSES)}")
    if not isinstance(args.get("message"), str):
        raise ToolCallError("invalid_message", "message must be a string")
    return {"accepted": True}


async def run_skeleton(
    model: ModelClient, *, episode_dir: Path, episode_id: str, seed: int = 0
) -> SkeletonResult:
    service = BillingService(
        episode_id=episode_id,
        journal=EffectJournal(episode_dir / "effects.jsonl"),
        db_path=episode_dir / "bank.sqlite",
        id_seed=seed,
    )
    service.add_invoices(DEMO_INVOICES)

    with running_server(service) as url:
        async with McpBackend(url) as bank:
            gateway = ToolGateway(
                episode_id=episode_id,
                journal=CallJournal(episode_dir / "calls.jsonl"),
                backends=[bank],
            )
            gateway.register(
                "finish", _finish, description=FINISH_DESCRIPTION, parameters=FINISH_PARAMS
            )
            spec = AgentSpec(
                actor=ACTOR,
                system_prompt=SYSTEM_PROMPT,
                max_steps=MAX_STEPS,
                tools=gateway.openai_tools([*sorted(TOOL_NAMES), "finish"]),
                finish_tools=frozenset({"finish"}),
            )
            log = JsonlWriter[ModelCallLog](episode_dir / "model_calls.jsonl")
            trace = await run_agent(spec, GOAL, model=model, gateway=gateway, log=log)
    service.close()

    (episode_dir / "trace.json").write_text(trace.model_dump_json(indent=2), encoding="utf-8")
    result = SkeletonResult(
        episode_dir=episode_dir,
        stop_reason=trace.stop_reason,
        steps=trace.steps,
        focal_charges=sum(r.invoice_id == FOCAL_INVOICE for r in service.journal.rows),
        finish_args=trace.finish_args,
        cost=trace.usage.cost,
        prompt_tokens=trace.usage.prompt_tokens,
        completion_tokens=trace.usage.completion_tokens,
        retries=trace.retries,
        error=trace.error,
    )
    (episode_dir / "summary.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
