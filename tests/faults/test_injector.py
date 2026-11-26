import json
from pathlib import Path
from typing import Any

import pytest
from helpers.episode import (
    CHARGE,
    DELEGATE,
    LIST,
    calls,
    delegate_result,
    effects,
    finish,
    report,
    run,
)

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.loop import ModelCallLog, rebuild_requests
from handoff_bench.jsonl import read_rows
from handoff_bench.worlds import BATCH_TEMPLATE, TEMPLATES, generate

pytestmark = pytest.mark.anyio

EMPTY = delegate_result("")


def orchestrator_requests(tmp_path: Path) -> str:
    """Everything the orchestrator's model was sent, as canonical JSON bytes."""
    rows = read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog)
    requests = {
        step: messages
        for (actor, step), messages in rebuild_requests(rows).items()
        if actor == "orchestrator"
    }
    tools = [r.tools for r in rows if r.actor == "orchestrator"]
    return json.dumps([requests, tools], sort_keys=True, ensure_ascii=False)


def subagents(tmp_path: Path) -> list[dict[str, Any]]:
    return json.loads((tmp_path / "ep" / "trace.json").read_text())["subagents"]


async def test_pre_and_post_give_the_orchestrator_byte_identical_input(tmp_path) -> None:
    # The subagents differ in steps and tokens: pre stops at the charge, post goes on.
    pre_script = [DELEGATE, CHARGE, finish("unknown")]
    post_script = [DELEGATE, CHARGE, LIST, report(), finish("unknown")]
    _, pre = await run(tmp_path / "pre", pre_script, fault="pre")
    _, post = await run(tmp_path / "post", post_script, fault="post")

    assert orchestrator_requests(tmp_path / "pre") == orchestrator_requests(tmp_path / "post")
    assert (pre.fault_fired, post.fault_fired) == (True, True)
    assert (len(effects(tmp_path / "pre")), len(effects(tmp_path / "post"))) == (0, 1)
    seen = [
        next(r.result for r in CallJournal.read(p / "ep" / "calls.jsonl") if r.tool == "delegate")
        for p in (tmp_path / "pre", tmp_path / "post")
    ]
    assert seen[0] == seen[1] == {"result": "", "status": "completed"}


@pytest.mark.parametrize("template", [t for t in TEMPLATES if t != BATCH_TEMPLATE])
async def test_equivalence_holds_on_generated_worlds(tmp_path, template) -> None:
    world = generate(1, 1, template)
    invoices = {inv.invoice_id: inv for inv in world.invoices}
    pay = [
        [("create_charge", invoices[t].model_dump(include={"invoice_id", "customer_id", "amount"}))]
        for t in world.targets
    ]
    delegate = [("delegate", {"goal": world.task})]
    _, pre = await run(
        tmp_path / "pre", [delegate, *pay, finish("unknown")], world=world, fault="pre"
    )
    _, post = await run(
        tmp_path / "post", [delegate, *pay, report(), finish("unknown")], world=world, fault="post"
    )

    assert orchestrator_requests(tmp_path / "pre") == orchestrator_requests(tmp_path / "post")
    assert [e.invoice_id for e in effects(tmp_path / "pre")] == world.targets[:-1]
    assert [e.invoice_id for e in effects(tmp_path / "post")] == world.targets


async def test_none_passes_the_report_through(tmp_path) -> None:
    model, result = await run(tmp_path, [DELEGATE, CHARGE, report(), finish()], fault="none")

    assert model.requests[3][-1]["content"] == delegate_result("Paid INV-12.")
    assert result.fault_fired is False


async def test_pre_drops_the_focal_charge_and_stops_the_subagent(tmp_path) -> None:
    model, _ = await run(tmp_path, [DELEGATE, CHARGE, finish("unknown")], fault="pre")

    (charge,) = [r for r in CallJournal.read(tmp_path / "ep" / "calls.jsonl") if r.actor == "sub-1"]
    assert (charge.tool, charge.outcome) == ("create_charge", "dropped")
    assert subagents(tmp_path)[0]["stop_reason"] == "dropped"
    assert model.requests[-1][-1]["content"] == EMPTY


async def test_post_loses_only_the_report_the_trace_keeps_it(tmp_path) -> None:
    model, _ = await run(tmp_path, [DELEGATE, CHARGE, report(), finish("unknown")], fault="post")

    (sub,) = subagents(tmp_path)
    assert (sub["stop_reason"], sub["finish_args"]["text"]) == ("finish", "Paid INV-12.")
    assert model.requests[-1][-1]["content"] == EMPTY


async def test_pre_fires_once_so_redelegation_pays(tmp_path) -> None:
    script = [DELEGATE, CHARGE, DELEGATE, CHARGE, report(), finish()]
    model, _ = await run(tmp_path, script, fault="pre")

    assert [e.actor for e in effects(tmp_path)] == ["sub-2"]
    assert model.requests[-1][-1]["content"] == delegate_result("Paid INV-12.")


async def test_post_fires_once_so_redelegation_duplicates(tmp_path) -> None:
    script = [DELEGATE, CHARGE, report(), DELEGATE, CHARGE, report(), finish()]
    model, _ = await run(tmp_path, script, fault="post")

    assert [e.actor for e in effects(tmp_path)] == ["sub-1", "sub-2"]
    assert model.requests[3][-1]["content"] == EMPTY
    assert model.requests[-1][-1]["content"] == delegate_result("Paid INV-12.")


async def test_fault_ignores_other_invoices(tmp_path) -> None:
    other = [("create_charge", {"invoice_id": "INV-13", "customer_id": "cus_bob", "amount": 4500})]
    await run(tmp_path, [DELEGATE, other, CHARGE, finish("unknown")], fault="pre")

    assert [e.invoice_id for e in effects(tmp_path)] == ["INV-13"]
    assert calls(tmp_path)[1:3] == [("sub-1", "create_charge"), ("sub-1", "create_charge")]


@pytest.mark.parametrize("fault", ["pre", "post"])
async def test_wrong_charge_is_not_a_fault_trigger(tmp_path, fault) -> None:
    # Same trigger in both modes: a wrong call fails in the bank and nothing is broken.
    wrong = [("create_charge", {"invoice_id": "INV-12", "customer_id": "cus_bob", "amount": 12000})]
    script = [DELEGATE, wrong, report("failed", "Wrong customer."), finish("failed")]
    model, result = await run(tmp_path, script, fault=fault)

    assert result.fault_fired is False
    assert "customer_mismatch" in model.requests[2][-1]["content"]
    assert model.requests[-1][-1]["content"] == delegate_result("Wrong customer.")


@pytest.mark.parametrize("amount", ["12000", 1200])
@pytest.mark.parametrize(("fault", "charged"), [("pre", 0), ("post", 1)])
async def test_any_call_the_bank_would_execute_is_a_trigger(
    tmp_path, amount, fault, charged
) -> None:
    # The bank coerces "12000" and does not check the amount against the invoice.
    pay = [("create_charge", {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": amount})]
    _, result = await run(tmp_path, [DELEGATE, pay, report(), finish("unknown")], fault=fault)

    assert result.fault_fired is True
    assert len(effects(tmp_path)) == charged
