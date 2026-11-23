from collections.abc import Callable

import pytest

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.mcp_backend import McpBackend
from handoff_bench.billing import BillingService, Invoice
from handoff_bench.faults.partial import PartialExecution


@pytest.mark.anyio
async def test_only_distinct_new_target_commits_by_the_first_payer_count(
    serve_bank: Callable[[BillingService], str],
) -> None:
    invoices = [
        Invoice(invoice_id=f"INV-{i}", customer_id="cus_ann", amount=1000) for i in range(4)
    ]
    targets = [inv.invoice_id for inv in invoices[:3]]
    with BillingService(episode_id="test") as service:
        service.add_invoices(invoices)
        service.create_charge(targets[2], "cus_ann", 1000, "earlier-key", actor="prior-payer")
        fault = PartialExecution(targets, 2, service.journal)
        async with McpBackend(serve_bank(service)) as backend:
            gateway = ToolGateway(
                episode_id="test", journal=CallJournal(), backends=[backend], hooks=[fault]
            )

            async def charge(invoice_id: str, actor: str = "sub-1", **overrides: object) -> str:
                result = await gateway.call(
                    actor,
                    "create_charge",
                    {
                        "invoice_id": invoice_id,
                        "customer_id": "cus_ann",
                        "amount": 1000,
                        **overrides,
                    },
                )
                return result.outcome

            assert await charge(targets[0], customer_id="wrong") == "error"
            assert await charge(invoices[3].invoice_id) == "ok"
            assert await charge(targets[0], idempotency_key="test-key", amount=999) == "ok"
            assert await charge(targets[0], idempotency_key="test-key", amount=999) == "ok"
            assert await charge(targets[2], idempotency_key="earlier-key") == "ok"
            assert len(service.journal.rows) == 3
            assert await charge(targets[0]) == "ok"  # A new charge, on a target already counted.
            assert not fault.fired
            assert await charge(targets[1]) == "dropped"
            assert fault.stop is not None
            assert fault.stop.charge_id == service.journal.rows[-1].charge_id
            assert fault.report_lost("sub-1")
            assert await charge(targets[2], actor="sub-2") == "ok"
            assert not fault.report_lost("sub-2")
