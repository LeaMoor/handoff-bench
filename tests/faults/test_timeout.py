import json
from pathlib import Path

import pytest
from helpers.episode import CHARGE, DELEGATE, LIST, effects, finish, report, run

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import ORCH_TOOLS_MAIN
from handoff_bench.agents.loop import ModelCallLog, rebuild_requests
from handoff_bench.channels.render import HEADER
from handoff_bench.grading.coder import code_behaviour
from handoff_bench.jsonl import read_rows

pytestmark = pytest.mark.anyio

WORLDS = ("timeout_lost", "timeout_committed")
UNKNOWN = report("unknown", "The bank timed out; the charge is not visible yet.")


def requests_of(tmp_path: Path, actor: str) -> str:
    rows = read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog)
    by_step = {s: m for (a, s), m in rebuild_requests(rows).items() if a == actor}
    return json.dumps(by_step, sort_keys=True, ensure_ascii=False)


async def run_both(tmp_path: Path, script: list, **kwargs):
    return {
        fault: await run(
            tmp_path / fault, script, fault=fault, orchestrator_tools=ORCH_TOOLS_MAIN, **kwargs
        )
        for fault in WORLDS
    }


async def test_subagent_and_orchestrator_see_the_same_in_both_worlds(tmp_path) -> None:
    # Two checks right after the timeout: the charge is not visible yet in either world.
    runs = await run_both(tmp_path, [DELEGATE, CHARGE, LIST, LIST, UNKNOWN, finish("unknown")])

    lost, committed = (tmp_path / w for w in WORLDS)
    assert requests_of(lost, "sub-1") == requests_of(committed, "sub-1")
    assert requests_of(lost, "orchestrator") == requests_of(committed, "orchestrator")
    model, _ = runs["timeout_lost"]
    assert model.requests[2][-1]["content"] == (
        "Error (timeout): Request to billing timed out; the outcome is unknown."
    )
    assert model.requests[3][-1]["content"] == "[]"
    assert (len(effects(lost)), len(effects(committed))) == (0, 1)
    assert all(result.fault_fired for _, result in runs.values())


async def test_charge_shows_up_three_bank_steps_later_only_if_committed(tmp_path) -> None:
    runs = await run_both(
        tmp_path, [DELEGATE, CHARGE, LIST, LIST, LIST, UNKNOWN, finish("unknown")]
    )

    third_check = {w: model.requests[5][-1]["content"] for w, (model, _) in runs.items()}
    assert third_check["timeout_lost"] == "[]"
    assert '"charge_id"' in third_check["timeout_committed"]


async def test_only_the_first_charge_times_out_so_a_blind_retry_can_duplicate(tmp_path) -> None:
    await run_both(tmp_path, [DELEGATE, CHARGE, CHARGE, report(), finish()])

    assert len(effects(tmp_path / "timeout_lost")) == 1
    assert len(effects(tmp_path / "timeout_committed")) == 2


async def test_code_channel_shows_the_timeout_and_the_checks(tmp_path) -> None:
    runs = await run_both(
        tmp_path, [DELEGATE, CHARGE, LIST, UNKNOWN, finish("unknown")], channel="code"
    )

    for model, _ in runs.values():
        assert json.loads(model.requests[-1][-1]["content"])["result"] == "\n".join(
            [
                HEADER,
                "- step 1: create_charge INV-12: timeout, outcome unknown",
                "- step 2: list_charges INV-12: no charges",
            ]
        )


async def test_no_charge_attempt_means_the_fault_did_not_fire(tmp_path) -> None:
    runs = await run_both(tmp_path, [DELEGATE, report("failed", "Gave up."), finish("failed")])

    assert not any(result.fault_fired for _, result in runs.values())


async def test_orchestrator_checks_itself_then_decides(tmp_path) -> None:
    # Main block: the orchestrator has list_charges. The charge (bank step 1) shows from step 4.
    orch_list = [("list_charges", {"invoice_id": "INV-12"})]
    script = [DELEGATE, CHARGE, UNKNOWN, orch_list, orch_list, orch_list, finish("done")]
    runs = await run_both(tmp_path, script)

    for world, (model, result) in runs.items():
        rows = CallJournal.read(tmp_path / world / "ep" / "calls.jsonl")
        behaviour = code_behaviour(
            rows,
            orchestrator="orchestrator",
            responses=result.orchestrator_responses,
            final_text=result.final_text,
        )
        assert (behaviour.first_action, behaviour.self_checked) == ("check", True)
        last_check = model.requests[-1][-1]["content"]
        assert (last_check == "[]") == (world == "timeout_lost")
