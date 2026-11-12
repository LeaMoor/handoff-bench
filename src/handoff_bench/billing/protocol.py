"""Контракт банка по сети — в одном месте для сервера и клиентов.

Сервер (billing/server.py) и клиентская сторона (agents/backends.py) импортируют отсюда,
поэтому формат ошибки, заголовок и путь нельзя поменять с одной стороны и забыть с другой.
Модуль лёгкий: без uvicorn и без MCP-сервера.
"""

ACTOR_HEADER = "x-actor"  # кто вызывает: ставит наш код, не модель
UNKNOWN_ACTOR = "unknown"
MCP_PATH = "/mcp"

TOOL_NAMES = frozenset({"get_invoice", "create_charge", "list_charges"})


def format_error(code: str, message: str) -> str:
    """Текст ошибки инструмента, который сервер отдаёт клиенту."""
    return f"{code}: {message}"


def parse_error(tool: str, text: str) -> tuple[str, str] | None:
    """Обратное к format_error. None — если текст не в нашем формате.

    MCP SDK добавляет к тексту ошибки префикс "Error executing tool <tool>: " — снимаем его.
    """
    text = text.removeprefix(f"Error executing tool {tool}: ")
    code, sep, message = text.partition(": ")
    if not sep or not code.isidentifier():
        return None
    return code, message
