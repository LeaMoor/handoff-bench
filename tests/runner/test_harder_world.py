"""The world where a check cannot help: long delay, no rules block, and B0 (a single agent)."""

import json
from pathlib import Path

import pytest
from helpers.episode import CHARGE, DELEGATE, GOAL, LIST, calls, effects, finish, report, run

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import SINGLE_TOOLS
from handoff_bench.agents.loop import ModelCallLog, rebuild_requests
from handoff_bench.jsonl import read_rows

pytestmark = pytest.mark.anyio

WORLDS = ("timeout_lost", "timeout_committed")
INVOICE = [("get_invoice", {"invoice_id": "INV-12"})]


def requests_of(tmp_path: Path, actor: str) -> str:
    rows = read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog)
    by_step = {s: m for (a, s), m in rebuild_requests(rows).items() if a == actor}
    return json.dumps(by_step, sort_keys=True, ensure_ascii=False)


async def test_long_delay_hides_the_charge_from_every_later_check(tmp_path) -> None:
    # The subagent and then the orchestrator check, again and again: nothing ever shows.
    script = [
        DELEGATE,
        CHARGE,
        LIST,
        report("unknown", "Timed out."),
        LIST,
        INVOICE,
        LIST,
        finish("unknown"),
    ]
    for world in WORLDS:
        await run(tmp_path / world, script, fault=world, delay="long", rules=False)

    lost, committed = (tmp_path / w for w in WORLDS)
    assert requests_of(lost, "orchestrator") == requests_of(committed, "orchestrator")
    assert (len(effects(lost)), len(effects(committed))) == (0, 1)
    rows = CallJournal.read(committed / "ep" / "calls.jsonl")
    assert [r.result for r in rows if r.tool == "list_charges"] == [[], [], []]
    assert next(r.result for r in rows if r.tool == "get_invoice")["status"] == "open"


async def test_without_rules_the_subagent_gets_only_the_goal(tmp_path) -> None:
    model, _ = await run(
        tmp_path, [DELEGATE, report(), finish()], fault="timeout_lost", rules=False
    )

    assert model.requests[1][1] == {"role": "user", "content": GOAL}


async def test_single_agent_pays_itself_and_meets_the_timeout(tmp_path) -> None:
    script = [CHARGE, LIST, finish("unknown")]
    model, result = await run(
        tmp_path,
        script,
        fault="timeout_committed",
        channel="none",
        delay="long",
        rules=False,
        agent="single",
    )

    assert result.fault_fired and result.delegations == 0
    assert calls(tmp_path) == [
        ("orchestrator", "create_charge"),
        ("orchestrator", "list_charges"),
        ("orchestrator", "finish"),
    ]
    tools = {
        r.actor: set(r.tools)
        for r in read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog)
    }
    assert tools == {"orchestrator": set(SINGLE_TOOLS)}
    assert len(effects(tmp_path)) == 1


async def test_calibration_without_the_loophole_a_delegated_check_sees_nothing(tmp_path) -> None:
    check = [("delegate", {"goal": "Read-only: look up INV-12 and its charges."})]
    script = [
        DELEGATE,
        CHARGE,
        report(),
        check,
        LIST,
        INVOICE,
        report("unknown"),
        finish("unknown"),
    ]
    await run(tmp_path, script, fault="post", delay="long")

    assert len(effects(tmp_path)) == 1
    rows = [r for r in CallJournal.read(tmp_path / "ep" / "calls.jsonl") if r.actor == "sub-2"]
    assert [r.result for r in rows if r.tool == "list_charges"] == [[]]
    assert next(r.result for r in rows if r.tool == "get_invoice")["status"] == "open"
