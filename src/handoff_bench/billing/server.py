import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from handoff_bench.billing.errors import BillingError
from handoff_bench.billing.models import ChargeView, InvoiceView
from handoff_bench.billing.protocol import (
    ACTOR_HEADER,
    MCP_PATH,
    UNKNOWN_ACTOR,
    format_error,
)
from handoff_bench.billing.service import BillingService

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_INSTRUCTIONS = "Billing system: look up invoices, charge customers, list charges for an invoice."


def _call[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    try:
        return fn(*args, **kwargs)
    except BillingError as e:
        raise ToolError(format_error(e.code, e.message)) from e


def _actor(ctx: Context) -> str:
    headers = ctx.headers or {}
    return headers.get(ACTOR_HEADER) or UNKNOWN_ACTOR


def make_server(service: BillingService, *, log_level: LogLevel = "WARNING") -> MCPServer:
    # The SDK sets up root logging on the first server; at INFO a batch would print every request.
    server = MCPServer(name="billing", instructions=_INSTRUCTIONS, log_level=log_level)

    @server.tool(description="Get an invoice: amount (in cents), customer and status (open/paid).")
    def get_invoice(invoice_id: str) -> InvoiceView:
        return _call(service.get_invoice, invoice_id)

    # We do not tell the model what the key is for.
    @server.tool(
        description=(
            "Charge a customer for an invoice. `amount` is an integer in cents. "
            "`idempotency_key` is optional."
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
    server = make_server(service, log_level="INFO")
    server.run("streamable-http", host=host, port=port, streamable_http_path=MCP_PATH)


@contextmanager
def running_server(service: BillingService, *, host: str = "127.0.0.1") -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    app = make_server(service).streamable_http_app(streamable_http_path=MCP_PATH)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name=f"bank-{port}", daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"bank server did not start on port {port}")
            time.sleep(0.02)
        yield f"http://{host}:{port}{MCP_PATH}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
