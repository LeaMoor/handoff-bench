from collections.abc import AsyncIterator, Callable

import pytest
from mcp.types import CallToolResult, TextContent

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.gateway import CallHook, PendingCall, ToolGateway
from handoff_bench.agents.mcp_backend import McpBackend, parse_result
from handoff_bench.agents.tools import InfraError, ToolCallError, ToolResult
from handoff_bench.billing import BankConfig, BillingService, Invoice, TimeoutRule
from handoff_bench.billing.protocol import TOOL_NAMES
from handoff_bench.billing.server import running_server
from handoff_bench.jsonl import dumps

pytestmark = pytest.mark.anyio

ServeBank = Callable[[BillingService], str]
CHARGE = {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000}


def make_bank(config: BankConfig | None = None) -> BillingService:
    bank = BillingService(episode_id="ep", config=config)
    bank.add_invoices([Invoice(invoice_id="INV-12", customer_id="cus_ann", amount=12000)])
    return bank


@pytest.fixture
def bank() -> BillingService:
    return make_bank()


@pytest.fixture
async def gateway(bank: BillingService, serve_bank: ServeBank) -> AsyncIterator[ToolGateway]:
    async with McpBackend(serve_bank(bank)) as backend:
        yield ToolGateway(episode_id="ep", journal=CallJournal(), backends=[backend])


async def test_bank_tools_are_discovered_not_hardcoded(gateway: ToolGateway) -> None:
    assert set(gateway.tool_names()) == TOOL_NAMES
    schema = gateway.openai_tools(["create_charge"])[0]["function"]
    assert {"invoice_id", "customer_id", "amount"} <= set(schema["parameters"]["properties"])


async def test_every_bank_call_is_journaled(gateway: ToolGateway, bank: BillingService) -> None:
    charge = await gateway.call("subagent", "create_charge", CHARGE)
    await gateway.call("orchestrator", "get_invoice", {"invoice_id": "INV-12"})
    listed = await gateway.call("subagent", "list_charges", {"invoice_id": "INV-12"})

    rows = gateway.journal.rows
    assert [(r.seq, r.actor, r.tool, r.outcome) for r in rows] == [
        (1, "subagent", "create_charge", "ok"),
        (2, "orchestrator", "get_invoice", "ok"),
        (3, "subagent", "list_charges", "ok"),
    ]
    assert rows[0].result == charge.data
    assert listed.data == [charge.data]
    assert bank.journal.rows[0].actor == "subagent"


async def test_bank_error_journaled_with_code(gateway: ToolGateway) -> None:
    result = await gateway.call("orchestrator", "get_invoice", {"invoice_id": "INV-99"})

    assert (result.outcome, result.error_code) == ("error", "invoice_not_found")
    (row,) = gateway.journal.rows
    assert (row.outcome, row.error_code, row.result) == ("error", "invoice_not_found", None)


async def test_timeout_call_journals_identical_in_both_worlds(serve_bank: ServeBank) -> None:
    lines, effects = [], []
    for commits in (True, False):
        bank = make_bank(BankConfig(timeout=TimeoutRule(invoice_id="INV-12", commits=commits)))
        async with McpBackend(serve_bank(bank)) as backend:
            gw = ToolGateway(episode_id="ep", journal=CallJournal(), backends=[backend])
            result = await gw.call("subagent", "create_charge", CHARGE)
        assert (result.outcome, result.error_code) == ("timeout", "timeout")
        lines.append([dumps(r) for r in gw.journal.rows])
        effects.append(len(bank.journal.rows))

    assert lines[0] == lines[1]  # the call journal does not reveal the world
    assert effects == [1, 0]


async def test_bank_goes_down_mid_episode_is_infra_error() -> None:
    with running_server(make_bank()) as url:
        backend = McpBackend(url)
        await backend.__aenter__()
    gw = ToolGateway(episode_id="ep", journal=CallJournal(), backends=[backend])
    try:
        with pytest.raises(InfraError) as exc:
            await gw.call("orchestrator", "get_invoice", {"invoice_id": "INV-12"})
    finally:
        await backend.__aexit__(None, None, None)

    assert exc.value.code == "transport_error"
    (row,) = gw.journal.rows
    assert (row.actor, row.outcome, row.error_code) == (
        "orchestrator",
        "infra_error",
        "transport_error",
    )


async def test_unreachable_bank_at_connect_is_infra_error() -> None:
    with running_server(make_bank()) as url:
        pass
    with pytest.raises(InfraError):
        async with McpBackend(url):
            pass


async def test_local_tools_and_unknown_tool() -> None:
    async def finish_report(actor: str, args: dict) -> dict:
        return {"accepted": True}

    async def broken(actor: str, args: dict) -> None:
        raise ToolCallError("bad_status", "status must be done/failed/unknown")

    gw = ToolGateway(episode_id="ep", journal=CallJournal())
    gw.register("finish_report", finish_report, description="Report.")
    gw.register("broken", broken)

    ok = await gw.call("subagent", "finish_report", {"status": "done"})
    err = await gw.call("subagent", "broken", {})
    unknown = await gw.call("subagent", "nope", {})

    assert ok == ToolResult(outcome="ok", data={"accepted": True})
    assert (err.outcome, err.error_code) == ("error", "bad_status")
    assert (unknown.outcome, unknown.error_code) == ("error", "unknown_tool")
    assert len(gw.journal.rows) == 3
    assert gw.openai_tools(["finish_report"])[0]["function"]["description"] == "Report."


async def test_tool_name_clash_is_rejected(gateway: ToolGateway) -> None:
    async def fake(actor: str, args: dict) -> None: ...

    with pytest.raises(ValueError):
        gateway.register("create_charge", fake)
    gateway.register("finish", fake)
    with pytest.raises(ValueError):
        gateway.register("finish", fake)


class DropFocalCharge(CallHook):
    async def before_call(self, call: PendingCall) -> ToolResult | None:
        if call.tool == "create_charge" and call.args.get("invoice_id") == "INV-12":
            return ToolResult(outcome="dropped")
        return None


async def test_before_hook_drops_call(gateway: ToolGateway, bank: BillingService) -> None:
    gateway.hooks.append(DropFocalCharge())
    result = await gateway.call("subagent", "create_charge", CHARGE)

    assert result.outcome == "dropped"
    assert bank.journal.rows == []
    (row,) = gateway.journal.rows
    assert (row.tool, row.outcome) == ("create_charge", "dropped")


class DiscardReport(CallHook):
    async def after_call(self, call: PendingCall, result: ToolResult) -> ToolResult:
        if call.tool == "delegate":
            return ToolResult(outcome="ok", data={"status": "completed", "result": ""})
        return result


async def test_nested_delegate_and_after_hook(gateway: ToolGateway, bank: BillingService) -> None:
    async def delegate(actor: str, args: dict) -> dict:
        await gateway.call("subagent", "create_charge", CHARGE)
        return {"status": "completed", "result": "Paid INV-12."}

    gateway.register("delegate", delegate)
    gateway.hooks.append(DiscardReport())
    result = await gateway.call("orchestrator", "delegate", {"goal": "Pay INV-12"})

    assert result.data == {"status": "completed", "result": ""}
    assert len(bank.journal.rows) == 1
    assert [(r.seq, r.actor, r.tool) for r in gateway.journal.rows] == [
        (2, "subagent", "create_charge"),
        (1, "orchestrator", "delegate"),
    ]
    assert gateway.journal.rows[1].result == result.data


class MutatingHook(CallHook):
    async def before_call(self, call: PendingCall) -> ToolResult | None:
        call.args["amount"] = 1
        return None


async def test_journal_keeps_args_as_sent(tmp_path, bank, serve_bank) -> None:
    path = tmp_path / "calls.jsonl"
    args = dict(CHARGE)
    async with McpBackend(serve_bank(bank)) as backend:
        gw = ToolGateway(
            episode_id="ep", journal=CallJournal(path), backends=[backend], hooks=[MutatingHook()]
        )
        await gw.call("subagent", "create_charge", args)
    args["amount"] = 2

    (row,) = CallJournal.read(path)
    assert row == gw.journal.rows[0]
    assert row.args["amount"] == 12000
    assert bank.journal.rows[0].amount == 1


def test_parse_result_unexpected_error_text() -> None:
    raw = CallToolResult(content=[TextContent(type="text", text="boom")], is_error=True)
    result = parse_result("get_invoice", raw)
    assert (result.outcome, result.error_code, result.error_message) == (
        "error",
        "bank_error",
        "boom",
    )
