import json

import pytest
from helpers.episode import CHARGE, DELEGATE, LIST, effects, finish, report, run

from handoff_bench.agents.loop import ModelCallLog
from handoff_bench.cells import CELLS
from handoff_bench.jsonl import read_rows

pytestmark = pytest.mark.anyio

WAIT = [("wait", {})]
ESCALATE = [("escalate", {"reason": "The bank timed out; I do not know if INV-12 was paid."})]


def sub_tools(tmp_path) -> set[str]:
    rows = read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog)
    return {t for r in rows if r.actor == "sub-1" for t in r.tools}


def test_cells_103_106_copy_12_13_24_25_with_safe_actions() -> None:
    for new, base in ((103, 12), (104, 13), (105, 24), (106, 25)):
        assert CELLS[new].safe_actions and not CELLS[base].safe_actions
        assert CELLS[new].model_dump(exclude={"number", "safe_actions"}) == CELLS[base].model_dump(
            exclude={"number", "safe_actions"}
        )


async def test_only_safe_action_cells_offer_wait_and_escalate(tmp_path) -> None:
    plain = [DELEGATE, report("unknown", "Timed out."), finish("unknown")]
    await run(tmp_path / "plain", plain, fault="timeout_lost", rules=False)
    safe = [DELEGATE, ESCALATE, finish("unknown")]
    await run(tmp_path / "safe", safe, fault="timeout_lost", rules=False, safe_actions=True)

    assert not {"wait", "escalate"} & sub_tools(tmp_path / "plain")
    assert {"wait", "escalate"} <= sub_tools(tmp_path / "safe")


async def test_escalate_ends_the_subagent_and_reaches_the_orchestrator_as_such(tmp_path) -> None:
    model, result = await run(
        tmp_path,
        [DELEGATE, CHARGE, ESCALATE, finish("unknown")],
        fault="timeout_committed",
        rules=False,
        safe_actions=True,
    )

    answer = json.loads(model.requests[-1][-1]["content"])
    assert answer == {"status": "escalated", "result": ESCALATE[0][1]["reason"]}
    trace = json.loads((tmp_path / "ep" / "trace.json").read_text())
    assert trace["subagents"][0]["finish_tool"] == "escalate"
    assert result.stop_reason == "finish"


@pytest.mark.parametrize(("delay", "seen"), [("short", 1), ("long", 0)])
async def test_wait_lets_a_check_see_a_committed_charge_only_with_the_short_delay(
    tmp_path, delay: str, seen: int
) -> None:
    model, _ = await run(
        tmp_path,
        [DELEGATE, CHARGE, WAIT, LIST, ESCALATE, finish("unknown")],
        fault="timeout_committed",
        delay=delay,
        rules=False,
        safe_actions=True,
    )

    listed = json.loads(model.requests[4][-1]["content"])
    assert len(listed) == seen
    assert len(effects(tmp_path)) == 1
