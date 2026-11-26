"""M8 runs the harness-assigned worklists together and reports in assignment order."""

from collections.abc import Callable, Mapping
from typing import Any

import anyio

from handoff_bench.agents.delegation import AgentsConfig, Delegation, Renderer
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentTrace, ModelCallLog
from handoff_bench.agents.model_client import ModelClient
from handoff_bench.agents.tools import EMPTY_PARAMS, ToolCallError
from handoff_bench.jsonl import JsonlWriter

PARALLEL_TOOLS = ("delegate_parallel", "finish")
PARALLEL_SYSTEM_PROMPT = (
    "Use delegate_parallel once to start both assigned worklists and collect their reports. "
    "After reading both reports, call finish with the overall outcome. "
    "Include the invoice ID in each entry of done or uncertain. "
    "Choose done only if each requested invoice was paid exactly once."
)


class ParallelDelegation(Delegation):
    """Dispatch the fixed worklists exactly once."""

    def __init__(
        self,
        gateway: ToolGateway,
        *,
        goals: Mapping[str, str],
        model: ModelClient,
        log: JsonlWriter[ModelCallLog] | None,
        render: Renderer,
        report_lost: Callable[[str], bool] | None,
        config: AgentsConfig,
        tasks: dict[str, str],
        hide_key: bool,
    ) -> None:
        super().__init__(
            gateway,
            model=model,
            log=log,
            render=render,
            report_lost=report_lost,
            config=config,
            tasks=tasks,
            hide_key=hide_key,
        )
        self._started = False
        self.overlap = False
        self._work = {self._reserve_actor(task): goal for task, goal in goals.items()}

    def _register_delegation_tool(self) -> None:
        self.gateway.register(
            "delegate_parallel",
            self._delegate_parallel,
            description="Start both assigned worklists concurrently and return their reports.",
            parameters=EMPTY_PARAMS,
        )

    @property
    def actors(self) -> tuple[str, ...]:
        """Actors with reserved task IDs for opening MCP sessions."""
        return tuple(self._work)

    async def _delegate_parallel(self, actor: str, args: dict[str, Any]) -> dict[str, Any]:
        if args:
            raise ToolCallError("invalid_arguments", "delegate_parallel takes no arguments")
        if self._started:
            raise ToolCallError("already_delegated", "Both worklists have already been delegated")
        self._started = True
        results: dict[str, AgentTrace] = {}
        active = 0

        async def run(worker: str, goal: str) -> None:
            nonlocal active
            active += 1
            self.overlap |= active > 1
            try:
                results[worker] = await self._run_actor(goal, worker)
            finally:
                active -= 1

        async with anyio.create_task_group() as group:
            for worker, goal in self._work.items():
                group.start_soon(run, worker, goal)

        self.subagents.extend(results[worker] for worker in self._work)
        return {
            "status": "completed",
            "reports": [
                {
                    "actor": child.actor,
                    "task_id": self.tasks[child.actor],
                    "result": self.render(
                        child, self.gateway.journal.by_actor(child.actor), self._lost_report(child)
                    ),
                }
                for child in self.subagents
            ],
        }
