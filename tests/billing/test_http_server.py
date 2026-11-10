"""Банк по настоящему HTTP: uvicorn на свободном порту, MCP-клиент с заголовком X-Actor."""

import socket
import threading
import time
from collections.abc import Iterator

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from handoff_bench.billing import BankConfig, BillingService, Invoice, TimeoutRule
from handoff_bench.billing.server import MCP_PATH, make_server

INVOICES = [Invoice(invoice_id="INV-12", customer_id="cus_ann", amount=12000)]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(service: BillingService) -> Iterator[str]:
    port = _free_port()
    app = make_server(service).streamable_http_app(streamable_http_path=MCP_PATH)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("bank server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}{MCP_PATH}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def bank() -> BillingService:
    service = BillingService(episode_id="ep-http")
    service.add_invoices(INVOICES)
    return service


@pytest.fixture
def url(bank: BillingService) -> Iterator[str]:
    yield from _start(bank)


async def call(url: str, tool: str, args: dict, actor: str | None = "subagent"):
    headers = {"X-Actor": actor} if actor else {}
    async with httpx2.AsyncClient(headers=headers) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            return await client.call_tool(tool, args)


CHARGE = {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000}


@pytest.mark.anyio
async def test_lists_three_tools(url: str) -> None:
    async with Client(url) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"get_invoice", "create_charge", "list_charges"}


@pytest.mark.anyio
async def test_charge_over_http_writes_one_effect_row(url: str, bank: BillingService) -> None:
    result = await call(url, "create_charge", CHARGE)

    assert not result.is_error
    (row,) = bank.journal.rows
    assert result.structured_content["charge_id"] == row.charge_id
    assert row.actor == "subagent"  # из заголовка X-Actor


@pytest.mark.anyio
async def test_missing_actor_header_is_unknown(url: str, bank: BillingService) -> None:
    await call(url, "create_charge", CHARGE, actor=None)
    assert bank.journal.rows[0].actor == "unknown"


@pytest.mark.anyio
async def test_list_charges_and_get_invoice(url: str) -> None:
    empty = await call(url, "list_charges", {"invoice_id": "INV-12"})
    await call(url, "create_charge", CHARGE)
    listed = await call(url, "list_charges", {"invoice_id": "INV-12"})
    invoice = await call(url, "get_invoice", {"invoice_id": "INV-12"})

    assert empty.structured_content == {"result": []}
    assert len(listed.structured_content["result"]) == 1
    assert invoice.structured_content["status"] == "paid"


@pytest.mark.anyio
async def test_billing_error_reaches_agent_with_code(url: str) -> None:
    result = await call(url, "get_invoice", {"invoice_id": "INV-99"})

    assert result.is_error
    assert "invoice_not_found" in result.content[0].text


@pytest.mark.anyio
async def test_timeout_identical_over_http_in_both_worlds() -> None:
    texts, rows = [], []
    for commits in (True, False):
        rule = TimeoutRule(invoice_id="INV-12", commits=commits)
        service = BillingService(episode_id="ep", config=BankConfig(timeout=rule))
        service.add_invoices(INVOICES)
        for url in _start(service):
            result = await call(url, "create_charge", CHARGE)
            assert result.is_error
            texts.append(result.model_dump_json())
        rows.append(len(service.journal.rows))

    assert texts[0] == texts[1]  # агент получает одно и то же байт в байт
    assert rows == [1, 0]
