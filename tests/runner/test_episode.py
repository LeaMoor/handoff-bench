import json
from pathlib import Path
from typing import Any

import pytest
from helpers.fake_model import FakeModel, Step

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import (
    ORCH_TOOLS_MAIN,
    SUB_MAX_STEPS,
    SUBAGENT_RULES,
    SUBAGENT_SYSTEM_PROMPT,
    AgentsConfig,
)
from handoff_bench.agents.loop import ModelCallLog, rebuild_requests
from handoff_bench.agents.model_client import ModelCallFailed
from handoff_bench.billing import EffectJournal
from handoff_bench.billing.demo import DEMO_INVOICES
from handoff_bench.jsonl import read_rows
from handoff_bench.runner.episode import EpisodeConfig, EpisodeResult, run_episode
from handoff_bench.worlds import World

pytestmark = pytest.mark.anyio

WORLD = World(
    world=1,
    template=1,
    seed=0,
    phrasing=0,
    customer_id="cus_ann",
    customer_name="Ann Lee",
    invoices=DEMO_INVOICES,
    targets=["INV-12"],
    focal_invoice="INV-12",
    task="Pay invoice INV-12 of customer Ann Lee (cus_ann) and report the result to me.",
)
GOAL = "Pay invoice INV-12 of customer cus_ann (12000 cents)."
DELEGATE = [("delegate", {"goal": GOAL})]
CHARGE = [("create_charge", {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000})]
LIST = [("list_charges", {"invoice_id": "INV-12"})]


def report(status: str = "done", text: str = "Paid INV-12.", **fields: object) -> Step:
    args = {"status": status, "done": [], "uncertain": [], "text": text, **fields}
    return [("finish_report", args)]


def finish(status: str = "done") -> Step:
    args = {"status": status, "done": [], "uncertain": [], "message": "Result."}
    return [("finish", args)]


def delegate_result(text: str) -> str:
    return json.dumps({"result": text, "status": "completed"})


async def run(
    tmp_path: Path,
    script: list[Step] | None,
    model: FakeModel | None = None,
    **kwargs: Any,
) -> tuple[FakeModel, EpisodeResult]:
    model = model or FakeModel(script or [])
    config = EpisodeConfig(
        episode_id="ep", cell=1, world=1, template=1, seed=0, agents=AgentsConfig(**kwargs)
    )
    result = await run_episode(model, WORLD, config, episode_dir=tmp_path / "ep")
    return model, result


def calls(tmp_path) -> list[tuple[str, str]]:
    rows = sorted(CallJournal.read(tmp_path / "ep" / "calls.jsonl"), key=lambda r: r.seq)
    return [(r.actor, r.tool) for r in rows]


def tools_by_actor(tmp_path) -> dict[str, set[str]]:
    rows = read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog)
    return {r.actor: set(r.tools) for r in rows}


async def test_orchestrator_delegates_subagent_pays_report_comes_back(tmp_path) -> None:
    model, result = await run(tmp_path, [DELEGATE, CHARGE, report(), finish()])

    assert (result.stop_reason, result.delegations) == ("finish", 1)
    assert result.finish_args["status"] == "done"
    assert result.cost == pytest.approx(0.004)
    (effect,) = EffectJournal.read(tmp_path / "ep" / "effects.jsonl")
    assert effect.actor == "sub-1"
    assert model.requests[3][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": delegate_result("Paid INV-12."),
    }
    assert calls(tmp_path) == [
        ("orchestrator", "delegate"),
        ("sub-1", "create_charge"),
        ("sub-1", "finish_report"),
        ("orchestrator", "finish"),
    ]
    trace = json.loads((tmp_path / "ep" / "trace.json").read_text())
    assert [s["actor"] for s in trace["subagents"]] == ["sub-1"]


async def test_orchestrator_cannot_finish_before_reading_the_report(tmp_path) -> None:
    model, result = await run(tmp_path, [DELEGATE + finish(), CHARGE, report(), finish()])

    assert (result.stop_reason, result.steps) == ("finish", 2)
    assert model.requests[3][-2]["content"] == delegate_result("Paid INV-12.")
    assert model.requests[3][-1]["content"].startswith("Error (finish_not_alone)")


async def test_subagent_sees_only_goal_rules_and_its_tools(tmp_path) -> None:
    model, _ = await run(tmp_path, [DELEGATE, report(), finish()])

    assert model.requests[1] == [
        {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"{GOAL}\n\n{SUBAGENT_RULES}"},
    ]
    assert tools_by_actor(tmp_path) == {
        "orchestrator": {"delegate", "finish"},
        "sub-1": {"get_invoice", "create_charge", "list_charges", "finish_report"},
    }


async def test_calibration_orchestrator_cannot_reach_bank(tmp_path) -> None:
    model, _ = await run(tmp_path, [LIST, finish("unknown")])

    assert model.requests[1][-1]["content"] == "Error (unknown_tool): No tool named list_charges."


async def test_main_block_orchestrator_can_check_bank(tmp_path) -> None:
    model, _ = await run(tmp_path, [LIST, finish("unknown")], orchestrator_tools=ORCH_TOOLS_MAIN)

    assert model.requests[1][-1]["content"] == "[]"
    assert tools_by_actor(tmp_path)["orchestrator"] == {
        "delegate",
        "get_invoice",
        "list_charges",
        "finish",
    }


async def test_redelegation_starts_a_new_subagent(tmp_path) -> None:
    script = [DELEGATE, report("failed", "Could not pay."), DELEGATE, CHARGE, report(), finish()]
    model, result = await run(tmp_path, script)

    assert result.delegations == 2
    (effect,) = EffectJournal.read(tmp_path / "ep" / "effects.jsonl")
    assert effect.actor == "sub-2"
    rebuilt = rebuild_requests(read_rows(tmp_path / "ep" / "model_calls.jsonl", ModelCallLog))
    assert rebuilt[("sub-2", 1)] == model.requests[3]


async def test_subagent_out_of_steps_gives_empty_result(tmp_path) -> None:
    get = [("get_invoice", {"invoice_id": "INV-12"})]
    model, _ = await run(tmp_path, [DELEGATE, *[get] * SUB_MAX_STEPS, finish("unknown")])

    assert model.requests[-1][-1]["content"] == delegate_result("")
    (sub,) = json.loads((tmp_path / "ep" / "trace.json").read_text())["subagents"]
    assert (sub["stop_reason"], sub["steps"]) == ("max_steps", SUB_MAX_STEPS)


async def test_subagent_plain_text_answer_is_passed_on(tmp_path) -> None:
    model, _ = await run(tmp_path, [DELEGATE, "I paid it.", finish()])

    assert model.requests[-1][-1]["content"] == delegate_result("I paid it.")


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ({"status": "great"}, "invalid_status"),
        ({"done": "INV-12"}, "invalid_done"),
        ({"uncertain": [1]}, "invalid_uncertain"),
        ({"text": None}, "invalid_text"),
    ],
)
async def test_bad_finish_report_is_rejected_and_subagent_retries(tmp_path, bad, code) -> None:
    script = [DELEGATE, [("finish_report", report()[0][1] | bad)], report(), finish()]
    model, _ = await run(tmp_path, script)

    assert model.requests[2][-1]["content"].startswith(f"Error ({code})")
    assert model.requests[3][-1]["content"] == delegate_result("Paid INV-12.")


async def test_subagent_api_error_ends_episode_without_telling_orchestrator(tmp_path) -> None:
    class FailsOnSecondCall(FakeModel):
        async def complete(self, messages, tools):
            if len(self.requests) == 1:
                raise ModelCallFailed(3, RuntimeError("503"))
            return await super().complete(messages, tools)

    model = FailsOnSecondCall([DELEGATE, finish()])
    _, result = await run(tmp_path, None, model=model)

    assert (result.stop_reason, result.retries) == ("api_error", 3)
    assert result.error == "RuntimeError('503')"
    assert len(model.requests) == 1
    (row,) = CallJournal.read(tmp_path / "ep" / "calls.jsonl")
    assert (row.tool, row.outcome) == ("delegate", "infra_error")
