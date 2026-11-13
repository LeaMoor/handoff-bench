import copy
from dataclasses import dataclass
from typing import Any

from handoff_bench.agents.call_journal import CallJournal, CallRow
from handoff_bench.agents.tools import (
    EMPTY_PARAMS,
    InfraError,
    LocalBackend,
    LocalTool,
    ToolBackend,
    ToolResult,
    ToolSpec,
    error_result,
)


@dataclass(frozen=True)
class PendingCall:
    seq: int
    actor: str
    tool: str
    args: dict[str, Any]


class CallHook:
    """Инжектор: pre — before_call возвращает dropped; post — after_call отмечает субагента,
    чей отчёт теряется (см. faults/injector.py)."""

    async def before_call(self, call: PendingCall) -> ToolResult | None:
        return None

    async def after_call(self, call: PendingCall, result: ToolResult) -> ToolResult:
        return result


class ToolGateway:
    def __init__(
        self,
        *,
        episode_id: str,
        journal: CallJournal,
        backends: list[ToolBackend] | None = None,
        hooks: list[CallHook] | None = None,
    ) -> None:
        self.episode_id = episode_id
        self.journal = journal
        self.hooks = list(hooks or [])
        self._local = LocalBackend()
        self._backends: list[ToolBackend] = [self._local]
        self._seq = 0
        for backend in backends or []:
            self.add_backend(backend)

    def add_backend(self, backend: ToolBackend) -> None:
        clash = {s.name for s in backend.specs()} & set(self.tool_names())
        if clash:
            raise ValueError(f"tool name clash: {sorted(clash)}")
        self._backends.append(backend)

    def register(
        self,
        name: str,
        fn: LocalTool,
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        if name in self.tool_names():
            raise ValueError(f"tool {name!r} is already provided")
        self._local.add(
            ToolSpec(name=name, description=description, parameters=parameters or EMPTY_PARAMS),
            fn,
        )

    def tool_names(self) -> list[str]:
        return [s.name for b in self._backends for s in b.specs()]

    def openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        specs = {s.name: s for b in self._backends for s in b.specs()}
        missing = set(names or []) - specs.keys()
        if missing:
            raise KeyError(f"unknown tools: {sorted(missing)}")
        return [specs[n].to_openai() for n in (names if names is not None else list(specs))]

    async def call(self, actor: str, tool: str, args: dict[str, Any]) -> ToolResult:
        self._seq += 1
        sent_args = copy.deepcopy(args)
        call = PendingCall(seq=self._seq, actor=actor, tool=tool, args=copy.deepcopy(args))

        try:
            result = await self._run(call)
        except InfraError as e:
            self._write(
                call.seq,
                actor,
                tool,
                sent_args,
                ToolResult(outcome="infra_error", error_code=e.code, error_message=e.message),
            )
            raise
        self._write(call.seq, actor, tool, sent_args, result)
        return result

    def reject(self, actor: str, tool: str, raw_args: str, code: str, message: str) -> ToolResult:
        self._seq += 1
        result = error_result(code, message)
        self._write(self._seq, actor, tool, {"_raw": raw_args}, result)
        return result

    async def _run(self, call: PendingCall) -> ToolResult:
        for hook in self.hooks:
            early = await hook.before_call(call)
            if early is not None:
                return early
        result = await self._execute(call)
        for hook in self.hooks:
            result = await hook.after_call(call, result)
        return result

    async def _execute(self, call: PendingCall) -> ToolResult:
        for backend in self._backends:
            if any(s.name == call.tool for s in backend.specs()):
                return await backend.call(call.actor, call.tool, call.args)
        return error_result("unknown_tool", f"No tool named {call.tool}.")

    def _write(
        self, seq: int, actor: str, tool: str, args: dict[str, Any], result: ToolResult
    ) -> None:
        self.journal.write(
            CallRow(
                episode_id=self.episode_id,
                seq=seq,
                actor=actor,
                tool=tool,
                args=args,
                outcome=result.outcome,
                result=copy.deepcopy(result.data),
                error_code=result.error_code,
                error_message=result.error_message,
            )
        )
