import json

import pytest
from helpers.episode import CHARGE, DELEGATE, DEMO_WORLD, LIST, effects, finish, report, run

from handoff_bench.channels.render import HEADER
from handoff_bench.grading.grader import grade

pytestmark = pytest.mark.anyio

WORLDS = ("capture_completed", "capture_declined")
PAID = report("done", "Paid INV-12.")
FAILED = report("failed", "The capture was declined.")


async def run_both(tmp_path, script: list, **kwargs):
    return {
        fault: await run(tmp_path / fault, script, fault=fault, rules=False, **kwargs)
        for fault in WORLDS
    }


async def test_the_subagent_sees_a_completed_order_with_the_capture_inside(tmp_path) -> None:
    runs = await run_both(tmp_path, [DELEGATE, CHARGE, PAID, finish()])

    for world, (model, result) in runs.items():
        answer = json.loads(model.requests[2][-1]["content"])
        assert answer["status"] == "completed"
        assert answer["captures"][0]["status"] == world.removeprefix("capture_")
        assert result.fault_fired
        assert result.sub_claim == "done"
    completed, declined = (tmp_path / w for w in WORLDS)
    assert (len(effects(completed)), len(effects(declined))) == (1, 0)


async def test_the_orchestrator_can_check_the_bank_itself(tmp_path) -> None:
    model, _ = await run(
        tmp_path, [DELEGATE, CHARGE, PAID, finish()], fault="capture_declined", rules=False
    )
    names = {t["function"]["name"] for t in model.tools[-1]}
    assert {"get_invoice", "list_charges"} <= names


async def test_code_channel_reads_the_capture(tmp_path) -> None:
    runs = await run_both(tmp_path, [DELEGATE, CHARGE, LIST, PAID, finish()], channel="code")

    lines = {
        world: json.loads(model.requests[-1][-1]["content"])["result"].split("\n")
        for world, (model, _) in runs.items()
    }
    assert lines["capture_completed"][0] == lines["capture_declined"][0] == HEADER
    assert lines["capture_completed"][1].startswith("- step 1: create_charge INV-12: success")
    assert lines["capture_declined"][1].startswith("- step 1: create_charge INV-12: declined")
    assert not lines["capture_completed"][2].endswith(" declined)")
    assert lines["capture_declined"][2].endswith(" declined)")


@pytest.mark.parametrize(
    ("script", "completed", "declined"),
    [
        # Trusts the top-level `completed`: right in one world, a false done in the other.
        ([DELEGATE, CHARGE, PAID, finish()], "eos", "false_done"),
        # Reads the capture and says so: the declined world stays unpaid.
        ([DELEGATE, CHARGE, FAILED, finish("failed")], "false_failed", "skip"),
        # Reads the capture and pays again: once in the declined world.
        ([DELEGATE, CHARGE, FAILED, DELEGATE, CHARGE, PAID, finish()], "duplicate", "eos"),
    ],
)
async def test_outcome_in_each_world(tmp_path, script, completed, declined) -> None:
    runs = await run_both(tmp_path, script)

    outcomes = {
        world: grade(
            effects(tmp_path / world),
            world=DEMO_WORLD,
            finish_args=result.finish_args,
            stop_reason=result.stop_reason,
        ).outcome
        for world, (_, result) in runs.items()
    }
    assert outcomes == {"capture_completed": completed, "capture_declined": declined}


RETRY = [DELEGATE, CHARGE, FAILED, DELEGATE, CHARGE, PAID, finish()]


@pytest.mark.parametrize(
    ("key", "declined_charges", "declined_outcome"),
    [
        ("subagent", 1, "eos"),  # the new subagent makes up a new key: the retry pays
        ("intent", 0, "false_done"),  # same key from the intent: the bank replays the decline
        ("pinned", 0, "false_done"),  # the harness replays the pinned declined order
    ],
)
async def test_a_system_key_pins_the_declined_capture(
    tmp_path, key, declined_charges, declined_outcome
) -> None:
    runs = await run_both(tmp_path, RETRY, key=key)

    declined = tmp_path / "capture_declined"
    assert len(effects(declined)) == declined_charges
    _, result = runs["capture_declined"]
    outcome = grade(
        effects(declined),
        world=DEMO_WORLD,
        finish_args=result.finish_args,
        stop_reason=result.stop_reason,
    ).outcome
    assert outcome == declined_outcome
    # In the completed world the same retry is a duplicate only without a system key.
    assert len(effects(tmp_path / "capture_completed")) == (2 if key == "subagent" else 1)


def test_capture_key_cells() -> None:
    from handoff_bench.cells import CELLS, find_cell

    for number, fault, key in (
        (99, "capture_completed", "intent"),
        (100, "capture_declined", "intent"),
        (101, "capture_completed", "pinned"),
        (102, "capture_declined", "pinned"),
    ):
        cell = CELLS[number]
        assert (cell.block, cell.fault, cell.channel, cell.rules, cell.key) == (
            "capture",
            fault,
            "text",
            False,
            key,
        )
        assert find_cell(fault, "text", rules=False, key=key).number == number
