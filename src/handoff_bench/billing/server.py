"""MCP-сервер банка поверх BillingService. Транспорт — Streamable HTTP (обычный HTTP-порт).

Один процесс сервера = один эпизод: своё состояние банка и свой журнал эффектов.

Кто вызывает (actor), сервер узнаёт из HTTP-заголовка `X-Actor`: оркестратор и субагент
подключаются к одному URL с разными заголовками. В аргументах инструментов actor нет,
поэтому модель не может его подделать. Нет заголовка — actor = "unknown".

Ошибки банка уходят агенту как ошибка инструмента с текстом "<code>: <message>".
"""

from collections.abc import Callable

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from handoff_bench.billing.errors import BillingError
from handoff_bench.billing.models import ChargeView, InvoiceView
from handoff_bench.billing.service import BillingService

ACTOR_HEADER = "x-actor"
UNKNOWN_ACTOR = "unknown"
MCP_PATH = "/mcp"

_INSTRUCTIONS = "Billing system: look up invoices, charge customers, list charges for an invoice."


def _call[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    try:
        return fn(*args, **kwargs)
    except BillingError as e:
        raise ToolError(f"{e.code}: {e.message}") from e


def _actor(ctx: Context) -> str:
    headers = ctx.headers or {}
    return headers.get(ACTOR_HEADER) or UNKNOWN_ACTOR


def make_server(service: BillingService) -> MCPServer:
    server = MCPServer(name="billing", instructions=_INSTRUCTIONS)

    @server.tool(description="Get an invoice: amount (in cents), customer and status (open/paid).")
    def get_invoice(invoice_id: str) -> InvoiceView:
        return _call(service.get_invoice, invoice_id)

    @server.tool(
        description=(
            "Charge a customer for an invoice. `amount` is an integer in cents. "
            "`idempotency_key` is optional: a repeated request with the same key and the same "
            "parameters returns the original result instead of creating a new charge."
        )
    )
    def create_charge(
        invoice_id: str,
        customer_id: str,
        amount: int,
        ctx: Context,
        idempotency_key: str | None = None,
    ) -> ChargeView:
        return _call(
            service.create_charge,
            invoice_id,
            customer_id,
            amount,
            idempotency_key,
            actor=_actor(ctx),
        )

    @server.tool(description="List all charges made for an invoice.")
    def list_charges(invoice_id: str) -> list[ChargeView]:
        return _call(service.list_charges, invoice_id)

    return server


def serve(service: BillingService, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Блокирующий запуск: http://<host>:<port>/mcp"""
    make_server(service).run("streamable-http", host=host, port=port, streamable_http_path=MCP_PATH)
