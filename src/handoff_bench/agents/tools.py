from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

Outcome = Literal["ok", "error", "timeout", "dropped", "rejected", "infra_error"]
StopReason = Literal["finish", "max_steps", "dropped", "text_only", "api_error", "infra_error"]
INVALID_STOPS: tuple[StopReason, ...] = ("api_error", "infra_error")


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Outcome
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None


def error_result(code: str, message: str) -> ToolResult:
    outcome: Outcome = "timeout" if code == "timeout" else "error"
    return ToolResult(outcome=outcome, error_code=code, error_message=message)


class ToolCallError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class InfraError(Exception):
    """Сбой стенда, а не ответ банка: агенту не показывается, эпизод не идёт в результаты."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolBackend(Protocol):
    def specs(self) -> list[ToolSpec]: ...

    async def call(self, actor: str, tool: str, args: dict[str, Any]) -> ToolResult: ...


LocalTool = Callable[[str, dict[str, Any]], Awaitable[Any]]

EMPTY_PARAMS: dict[str, Any] = {"type": "object", "properties": {}}


class LocalBackend:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, LocalTool]] = {}

    def add(self, spec: ToolSpec, fn: LocalTool) -> None:
        self._tools[spec.name] = (spec, fn)

    def specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values()]

    async def call(self, actor: str, tool: str, args: dict[str, Any]) -> ToolResult:
        _, fn = self._tools[tool]
        try:
            data = await fn(actor, args)
        except ToolCallError as e:
            return error_result(e.code, e.message)
        return ToolResult(outcome="ok", data=data)
