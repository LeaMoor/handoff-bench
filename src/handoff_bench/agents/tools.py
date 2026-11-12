"""Общие типы инструментов: что агент получает в ответ, ошибки, описание инструмента, бэкенды.

Бэкенд — источник инструментов для шлюза. Сейчас их два:
    LocalBackend  — функции Python (finish, позже delegate и finish_report);
    McpBackend    — инструменты MCP-сервера (банк), см. agents/mcp_backend.py.
Шлюз про банк ничего не знает: он спрашивает у бэкендов, какие у них инструменты, и вызывает.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

# ok — успех; error — инструмент вернул ошибку; timeout — банк ответил таймаутом;
# dropped — вызов не исполнялся (его перехватил инжектор), агенту ничего не вернулось;
# infra_error — сбой нашей инфраструктуры (нет связи с банком): эпизод недействителен.
Outcome = Literal["ok", "error", "timeout", "dropped", "infra_error"]


class ToolResult(BaseModel):
    """То, что получает агент в ответ на вызов."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Outcome
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None


def error_result(code: str, message: str) -> ToolResult:
    outcome: Outcome = "timeout" if code == "timeout" else "error"
    return ToolResult(outcome=outcome, error_code=code, error_message=message)


class ToolCallError(Exception):
    """Инструмент сообщает об ошибке так — агент получит outcome=error с кодом."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class InfraError(Exception):
    """Сбой инфраструктуры стенда (сеть, сервер банка). Агенту не показывается, эпизод
    останавливается с stop_reason="infra_error" и не идёт в результаты."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ToolSpec(BaseModel):
    """Описание инструмента для модели."""

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

    async def call(self, actor: str, tool: str, args: dict[str, Any]) -> ToolResult:
        """Ошибка инструмента — ToolResult (error/timeout); сбой связи — raise InfraError."""
        ...


LocalTool = Callable[[str, dict[str, Any]], Awaitable[Any]]
"""Локальный инструмент: (actor, args) -> данные ответа; ошибка — raise ToolCallError."""

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
