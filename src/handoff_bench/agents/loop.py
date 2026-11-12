"""Агентный цикл — один на обе роли (оркестратор и субагент).

    system + goal → модель → вызовы инструментов через шлюз → результаты → модель → …
    до finish-инструмента или лимита шагов (шаг = один вызов модели).

Каждый вызов модели пишется в журнал model_calls: новые сообщения запроса, ответ целиком,
токены, цена, повторы. Полный запрос восстанавливается функцией rebuild_requests().
"""

import copy
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.model_client import ModelCallFailed, ModelClient, ModelResponse, Usage
from handoff_bench.agents.tools import InfraError, ToolResult
from handoff_bench.jsonl import JsonlWriter

StopReason = Literal["finish", "max_steps", "dropped", "text_only", "api_error", "infra_error"]


class AgentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str  # "orchestrator" | "subagent" | …
    system_prompt: str
    max_steps: int
    tools: list[dict[str, Any]]  # схемы инструментов в формате OpenAI (gateway.openai_tools)
    finish_tools: frozenset[str]  # успешный вызов одного из них завершает агента


class ModelCallLog(BaseModel):
    episode_id: str
    actor: str
    step: int
    # Сообщения, добавленные в историю после предыдущего вызова модели этого агента.
    # Полный запрос шага = все new_messages шагов 1..step (см. rebuild_requests).
    new_messages: list[dict[str, Any]]
    history_len: int  # длина полного запроса — для проверки восстановления
    tools: list[str]
    response: dict[str, Any] | None
    usage: Usage
    retries: int
    latency_s: float
    error: str | None = None


def rebuild_requests(rows: list[ModelCallLog]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """Полные запросы к модели из журнала: {(actor, step): messages}."""
    history: dict[str, list[dict[str, Any]]] = {}
    out: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: (r.actor, r.step)):
        msgs = history.setdefault(row.actor, [])
        msgs.extend(row.new_messages)
        if len(msgs) != row.history_len:
            raise ValueError(f"log of {row.actor} is broken at step {row.step}")
        out[(row.actor, row.step)] = list(msgs)
    return out


class AgentTrace(BaseModel):
    actor: str
    stop_reason: StopReason
    steps: int
    finish_tool: str | None = None
    finish_args: dict[str, Any] | None = None
    messages: list[dict[str, Any]]
    usage: Usage
    retries: int
    error: str | None = None  # api_error / infra_error: что случилось


def render_tool_result(result: ToolResult) -> str:
    """Как результат вызова выглядит для модели. Детерминированно — для эквивалентности."""
    if result.outcome == "ok":
        return json.dumps(result.data, sort_keys=True, ensure_ascii=False)
    return f"Error ({result.error_code}): {result.error_message}"


async def run_agent(
    spec: AgentSpec,
    goal: str,
    *,
    model: ModelClient,
    gateway: ToolGateway,
    log: JsonlWriter[ModelCallLog] | None = None,
) -> AgentTrace:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": spec.system_prompt},
        {"role": "user", "content": goal},
    ]
    tool_names = [t["function"]["name"] for t in spec.tools]
    usage, retries, step, logged = Usage(), 0, 0, 0

    def trace(reason: StopReason, **extra: Any) -> AgentTrace:
        return AgentTrace(
            actor=spec.actor,
            stop_reason=reason,
            steps=step,
            messages=copy.deepcopy(messages),
            usage=usage,
            retries=retries,
            **extra,
        )

    def write_log(resp: ModelResponse | None, error: ModelCallFailed | None) -> None:
        nonlocal logged
        if log is None:
            return
        log.write(
            ModelCallLog(
                episode_id=gateway.episode_id,
                actor=spec.actor,
                step=step,
                new_messages=copy.deepcopy(messages[logged:]),
                history_len=len(messages),
                tools=tool_names,
                response=resp.raw if resp else None,
                usage=resp.usage if resp else Usage(),
                retries=resp.retries if resp else (error.retries if error else 0),
                latency_s=resp.latency_s if resp else 0.0,
                error=repr(error.error) if error else None,
            )
        )
        logged = len(messages)

    while step < spec.max_steps:
        step += 1
        try:
            resp = await model.complete(messages, spec.tools)
        except ModelCallFailed as e:
            retries += e.retries
            write_log(None, e)
            return trace("api_error", error=repr(e.error))

        usage, retries = usage + resp.usage, retries + resp.retries
        write_log(resp, None)
        messages.append(resp.message)

        if not resp.tool_calls:
            return trace("text_only")

        for tc in resp.tool_calls:
            try:
                args = json.loads(tc.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                result = gateway.reject(
                    spec.actor,
                    tc.name,
                    tc.arguments,
                    "invalid_arguments",
                    "Arguments must be a JSON object.",
                )
            else:
                try:
                    result = await gateway.call(spec.actor, tc.name, args)
                except InfraError as e:
                    # Наш сбой, не поведение модели: агенту не показываем, эпизод недействителен.
                    return trace("infra_error", error=str(e))

            if result.outcome == "dropped":
                # Инжектор pre: вызов не исполнен, агент останавливается без ответа.
                return trace("dropped")
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": render_tool_result(result)}
            )
            if result.outcome == "ok" and tc.name in spec.finish_tools:
                return trace("finish", finish_tool=tc.name, finish_args=args)

    return trace("max_steps")
