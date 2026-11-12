"""Банк по настоящему HTTP: uvicorn на свободном порту, MCP-клиент с заголовком X-Actor."""

from collections.abc import Callable

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from handoff_bench.billing import BankConfig, BillingService, Invoice, TimeoutRule
from handoff_bench.billing.protocol import TOOL_NAMES
from handoff_bench.billing.server import running_server

ServeBank = Callable[[BillingService], str]
INVOICES = [Invoice(invoice_id="INV-12", customer_id="cus_ann", amount=12000)]
CHARGE = {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000}


@pytest.fixture
def bank() -> BillingService:
    service = BillingService(episode_id="ep-http")
    service.add_invoices(INVOICES)
    return service


@pytest.fixture
def url(bank: BillingService, serve_bank: ServeBank) -> str:
    return serve_bank(bank)


async def call(url: str, tool: str, args: dict, actor: str | None = "subagent"):
    headers = {"X-Actor": actor} if actor else {}
    async with httpx2.AsyncClient(headers=headers) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            return await client.call_tool(tool, args)


@pytest.mark.anyio
async def test_tools_match_protocol_and_key_description_is_neutral(url: str) -> None:
    async with Client(url) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOL_NAMES
    # Модели не подсказываем, зачем нужен ключ (DECISIONS.md).
    description = tools["create_charge"].description.lower()
    assert "idempotency_key` is optional." in description
    assert "same key" not in description and "original" not in description


def test_parallel_episodes_get_distinct_ports() -> None:
    from contextlib import ExitStack

    with ExitStack() as stack:
        urls = [
            stack.enter_context(running_server(BillingService(episode_id=str(i)))) for i in range(8)
        ]
    assert len(set(urls)) == 8


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
async def test_timeout_identical_over_http_in_both_worlds(serve_bank: ServeBank) -> None:
    texts, rows = [], []
    for commits in (True, False):
        rule = TimeoutRule(invoice_id="INV-12", commits=commits)
        service = BillingService(episode_id="ep", config=BankConfig(timeout=rule))
        service.add_invoices(INVOICES)
        result = await call(serve_bank(service), "create_charge", CHARGE)
        assert result.is_error
        texts.append(result.model_dump_json())
        rows.append(len(service.journal.rows))

    assert texts[0] == texts[1]  # агент получает одно и то же байт в байт
    assert rows == [1, 0]
