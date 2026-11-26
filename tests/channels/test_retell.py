import json

import pytest
from helpers.episode import CHARGE, DELEGATE, LIST, finish, report, run

from handoff_bench.agents.delegation import RETELL_SYSTEM_PROMPT
from handoff_bench.agents.loop import ModelCallLog
from handoff_bench.jsonl import read_rows

pytestmark = pytest.mark.anyio

SUMMARY = "The subagent tried to charge INV-12; the bank timed out."
TIMED_OUT = report("unknown", "The charge timed out; do not retry.", uncertain=["charge INV-12"])


def retell_requests(model) -> list[list[dict]]:
    return [r for r in model.requests if r[0]["content"] == RETELL_SYSTEM_PROMPT]


@pytest.mark.parametrize("fault", ["timeout_lost", "timeout_committed"])
async def test_orchestrator_gets_the_retell_not_the_words(tmp_path, fault) -> None:
    script = [DELEGATE, CHARGE, TIMED_OUT, SUMMARY, finish("unknown")]
    model, result = await run(tmp_path, script, fault=fault, channel="retell")

    (request,) = retell_requests(model)
    run_text = request[-1]["content"]
    assert run_text.startswith("Task: Pay invoice INV-12")
    assert 'Call create_charge({"invoice_id": "INV-12"' in run_text
    assert "timeout" in run_text and "do not retry" in run_text
    assert model.tools[model.requests.index(request)] == []
    assert json.loads(model.requests[-1][-1]["content"])["result"] == SUMMARY
    assert result.cost == pytest.approx(0.005)  # five model calls, the retell included


async def test_retell_call_is_logged_under_its_own_actor(tmp_path) -> None:
    script = [DELEGATE, CHARGE, TIMED_OUT, SUMMARY, finish("unknown")]
    await run(tmp_path, script, fault="timeout_lost", channel="retell")

    rows = read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog)
    assert [r.actor for r in rows] == [
        "orchestrator",
        "sub-1",
        "sub-1",
        "retell-sub-1",
        "orchestrator",
    ]


async def test_retell_input_is_identical_in_both_worlds_with_long_delay(tmp_path) -> None:
    script = [DELEGATE, CHARGE, LIST, TIMED_OUT, SUMMARY, finish("unknown")]
    seen = []
    for fault in ("timeout_lost", "timeout_committed"):
        model, _ = await run(
            tmp_path / fault, script, fault=fault, channel="retell", delay="long", rules=False
        )
        seen.append(retell_requests(model))

    assert seen[0] == seen[1]
    assert 'Call list_charges({"invoice_id": "INV-12"}) -> []' in seen[0][0][-1]["content"]
