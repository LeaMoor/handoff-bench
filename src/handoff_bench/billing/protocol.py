ACTOR_HEADER = "x-actor"  # ставит стенд, а не модель: actor не должен попасть в аргументы
UNKNOWN_ACTOR = "unknown"
MCP_PATH = "/mcp"

TOOL_NAMES = frozenset({"get_invoice", "create_charge", "list_charges"})


def format_error(code: str, message: str) -> str:
    return f"{code}: {message}"


def parse_error(tool: str, text: str) -> tuple[str, str] | None:
    text = text.removeprefix(f"Error executing tool {tool}: ")
    code, sep, message = text.partition(": ")
    if not sep or not code.isidentifier():
        return None
    return code, message
