"""Orchestrator → subagent handoff: `delegate(goal)`, the subagent's `finish_report`
and the orchestrator's `finish`."""

import copy
from collections.abc import Callable, Mapping
from typing import Any

import anyio
from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.call_journal import CallRow
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, AgentTrace, ModelCallLog, run_agent
from handoff_bench.agents.model_client import ModelClient
from handoff_bench.agents.tools import EMPTY_PARAMS, INVALID_STOPS, InfraError, ToolCallError
from handoff_bench.jsonl import JsonlWriter

STATUSES = ("done", "failed", "unknown")

SUBAGENT_TOOLS = ("get_invoice", "create_charge", "list_charges", "finish_report")
# DECISIONS 2026-11-12: in calibration the orchestrator cannot check the bank itself.
ORCH_TOOLS_CALIBRATION = ("delegate", "finish")
ORCH_TOOLS_MAIN = ("delegate", "get_invoice", "list_charges", "finish")
# B0: the orchestrator works without a subagent and pays itself.
SINGLE_TOOLS = ("get_invoice", "create_charge", "list_charges", "finish")
PARALLEL_TOOLS = ("delegate_parallel", "finish")
ORCH_MAX_STEPS = 15
SUB_MAX_STEPS = 10

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are an assistant. Use the tools to complete the user's task. "
    "`delegate` hands a goal to a subagent and returns its report. "
    "When you are done, call `finish` with the outcome."
)
PARALLEL_SYSTEM_PROMPT = (
    "Use delegate_parallel once to start both assigned worklists and collect their reports. "
    "After reading both reports, call finish with the overall outcome. "
    "Include the invoice ID in each entry of done or uncertain. "
    "Choose done only if each requested invoice was paid exactly once."
)
SINGLE_SYSTEM_PROMPT = (
    "You are an assistant with access to a billing system. Use the tools to complete the user's "
    "task. When you are done, call `finish` with the outcome."
)
SUBAGENT_SYSTEM_PROMPT = (
    "You are an assistant with access to a billing system. Use the tools to complete the task. "
    "When you are done, call `finish_report` with the outcome."
)
# Appended to every goal by the stand, not by the orchestrator.
SUBAGENT_RULES = (
    "Rules: do every action exactly once. If the result of an action is unknown, "
    "check it with list_charges and say so in your report."
)

DELEGATE_DESCRIPTION = (
    "Hand a goal to a subagent that has access to the billing system. "
    "Waits until the subagent finishes and returns its report."
)
DELEGATE_PARAMS = {
    "type": "object",
    "properties": {"goal": {"type": "string"}},
    "required": ["goal"],
    "additionalProperties": False,
}


def _report_params(text_field: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": list(STATUSES)},
            "done": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Actions that were completed.",
            },
            "uncertain": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Actions whose outcome is unknown.",
            },
            text_field: {"type": "string"},
        },
        "required": ["status", "done", "uncertain", text_field],
        "additionalProperties": False,
    }


def _check_report(args: dict[str, Any], text_field: str) -> None:
    if args.get("status") not in STATUSES:
        raise ToolCallError("invalid_status", f"status must be one of {', '.join(STATUSES)}")
    for name in ("done", "uncertain"):
        value = args.get(name)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ToolCallError(f"invalid_{name}", f"{name} must be a list of strings")
    if not isinstance(args.get(text_field), str):
        raise ToolCallError(f"invalid_{text_field}", f"{text_field} must be a string")


def _register_report_tool(
    gateway: ToolGateway, name: str, text_field: str, description: str
) -> None:
    async def report(actor: str, args: dict[str, Any]) -> dict[str, Any]:
        _check_report(args, text_field)
        return {"accepted": True}

    gateway.register(name, report, description=description, parameters=_report_params(text_field))


def register_finish(gateway: ToolGateway) -> None:
    """The orchestrator's `finish(status, done, uncertain, message)`."""
    _register_report_tool(
        gateway, "finish", "message", "Finish the task and report the outcome to the user."
    )


class AgentsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    orchestrator_tools: tuple[str, ...] = ORCH_TOOLS_CALIBRATION
    orchestrator_prompt: str = ORCHESTRATOR_SYSTEM_PROMPT
    orchestrator_max_steps: int = ORCH_MAX_STEPS
    subagent_tools: tuple[str, ...] = SUBAGENT_TOOLS
    subagent_prompt: str = SUBAGENT_SYSTEM_PROMPT
    subagent_rules: str = SUBAGENT_RULES
    subagent_max_steps: int = SUB_MAX_STEPS


# What `delegate` hands the orchestrator as `result`: the channel (tasks 9, 16).
# `report_lost` (post fault) drops only the subagent's words; the trace and the journal keep them.
Renderer = Callable[[AgentTrace, list[CallRow], bool], Any]


class Delegation:
    """Registers delegation and reporting tools and keeps each subagent trace."""

    def __init__(
        self,
        gateway: ToolGateway,
        *,
        model: ModelClient,
        log: JsonlWriter[ModelCallLog] | None = None,
        render: Renderer,
        report_lost: Callable[[str], bool] | None = None,
        config: AgentsConfig | None = None,
        deliveries: int = 1,
        respawn_on_lost: bool = False,
        tasks: dict[str, str] | None = None,
        hide_key: bool = False,
        parallel_goals: Mapping[str, str] | None = None,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.log = log
        self.report_lost = report_lost
        self.config = config or AgentsConfig()
        self.render = render
        self.deliveries = deliveries
        self.respawn_on_lost = respawn_on_lost
        self.tasks = tasks if tasks is not None else {}  # subagent → the task it runs
        self.hide_key = hide_key
        self.subagents: list[AgentTrace] = []
        self._task_count = 0
        self._actor_count = 0
        self._parallel_started = False
        self.parallel_overlap = False
        self._parallel_work = {
            self._reserve_actor(task): goal for task, goal in (parallel_goals or {}).items()
        }
        if self._parallel_work:
            gateway.register(
                "delegate_parallel",
                self._delegate_parallel,
                description="Start both assigned worklists concurrently and return their reports.",
                parameters=EMPTY_PARAMS,
            )
        else:
            gateway.register(
                "delegate",
                self._delegate,
                description=DELEGATE_DESCRIPTION,
                parameters=DELEGATE_PARAMS,
            )
        _register_report_tool(
            gateway, "finish_report", "text", "Finish the task and report the outcome."
        )

    def _new_task(self) -> str:
        self._task_count += 1
        return f"t{self._task_count}"

    def _reserve_actor(self, task: str) -> str:
        self._actor_count += 1
        actor = f"sub-{self._actor_count}"
        self.tasks[actor] = task
        return actor

    @property
    def parallel_actors(self) -> tuple[str, ...]:
        """Actors with reserved task IDs for the parallel worklists."""
        return tuple(self._parallel_work)

    def _subagent_tools(self) -> list[dict[str, Any]]:
        tools = self.gateway.openai_tools(list(self.config.subagent_tools))
        if not self.hide_key:
            return tools
        out = copy.deepcopy(tools)
        for tool in out:
            tool["function"]["parameters"].get("properties", {}).pop("idempotency_key", None)
        return out

    async def _run_subagent(self, goal: str, actor: str) -> AgentTrace:
        spec = AgentSpec(
            actor=actor,
            system_prompt=self.config.subagent_prompt,
            max_steps=self.config.subagent_max_steps,
            tools=self._subagent_tools(),
            finish_tools=frozenset({"finish_report"}),
        )
        return await run_agent(
            spec,
            f"{goal}\n\n{self.config.subagent_rules}" if self.config.subagent_rules else goal,
            model=self.model,
            gateway=self.gateway,
            log=self.log,
        )

    def _report(self, child: AgentTrace) -> Any:
        if child.stop_reason in INVALID_STOPS:
            # A stand failure, not the subagent's behaviour: stop the episode, show nothing.
            raise InfraError(child.stop_reason, child.error or "")
        lost = self.report_lost is not None and self.report_lost(child.actor)
        return self.render(child, self.gateway.journal.by_actor(child.actor), lost)

    async def _delegate_parallel(self, actor: str, args: dict[str, Any]) -> dict[str, Any]:
        if args:
            raise ToolCallError("invalid_arguments", "delegate_parallel takes no arguments")
        if self._parallel_started:
            raise ToolCallError("already_delegated", "Both worklists have already been delegated")
        self._parallel_started = True
        results: dict[str, AgentTrace] = {}
        active = 0

        async def run(worker: str, goal: str) -> None:
            nonlocal active
            active += 1
            self.parallel_overlap |= active > 1
            try:
                results[worker] = await self._run_subagent(goal, worker)
            finally:
                active -= 1

        async with anyio.create_task_group() as group:
            for worker, goal in self._parallel_work.items():
                group.start_soon(run, worker, goal)

        children = [results[worker] for worker in self._parallel_work]
        self.subagents.extend(children)
        return {
            "status": "completed",
            "reports": [
                {
                    "actor": child.actor,
                    "task_id": self.tasks[child.actor],
                    "result": self._report(child),
                }
                for child in children
            ],
        }

    async def _delegate(self, actor: str, args: dict[str, Any]) -> dict[str, Any]:
        goal = args.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise ToolCallError("invalid_goal", "goal must be a non-empty string")
        task = self._new_task()
        # M4: the harness itself repeats the work — a double delivery runs the same task again,
        # a respawn after a lost report runs it as a new task.
        for _ in range(self.deliveries):
            child = await self._run_subagent(goal, self._reserve_actor(task))
            self.subagents.append(child)
            if child.stop_reason in INVALID_STOPS:
                raise InfraError(child.stop_reason, child.error or "")
        if self.respawn_on_lost and self.report_lost and self.report_lost(child.actor):
            child = await self._run_subagent(goal, self._reserve_actor(self._new_task()))
            self.subagents.append(child)
        return {"status": "completed", "result": self._report(child)}
