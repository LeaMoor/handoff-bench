"""Claude Code as the orchestrator, with a scripted fake `claude`: the stand's side of it."""

import json
import sys
from pathlib import Path

import pytest
from helpers.episode import DEMO_WORLD, GOAL, effects, report
from helpers.fake_model import FakeModel

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.cells import find_cell
from handoff_bench.harness.claude_code import (
    STREAM_FILE,
    ClaudeCodeOrchestrator,
    HarnessSettings,
)
from handoff_bench.runner.episode import EpisodeConfig, run_episode

pytestmark = pytest.mark.anyio

FAKE = Path(__file__).with_name("fake_claude.py")
FINISH = ["finish", {"status": "done", "done": [], "uncertain": [], "message": "Paid."}]
DELEGATE = ["delegate", {"goal": GOAL}]
CHARGE = [("create_charge", {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000})]


def fake_binary(tmp_path: Path) -> str:
    path = tmp_path / "claude"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    path.chmod(0o755)
    return str(path)


async def run(tmp_path, monkeypatch, plan, subagent, *, fault="none", key="subagent"):
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(plan))
    settings = HarnessSettings(model="fake", binary=fake_binary(tmp_path), finish_grace_s=5)
    cell = find_cell(fault, "text", key=key)
    config = EpisodeConfig(cell=cell.number, template=1, seed=0, orchestrator=settings)
    return await run_episode(
        FakeModel(subagent),
        DEMO_WORLD,
        config,
        episode_dir=tmp_path / "ep",
        orchestrator=ClaudeCodeOrchestrator(settings),
    )


async def test_harness_delegates_through_the_stand_and_finishes(tmp_path, monkeypatch) -> None:
    result = await run(tmp_path, monkeypatch, [[DELEGATE], [FINISH]], [CHARGE, report()])

    assert result.stop_reason == "finish"
    assert result.finish_args == FINISH[1]
    assert result.delegations == 1
    assert result.orchestrator_responses == [1, 1]
    rows = sorted(CallJournal.read(tmp_path / "ep" / "calls.jsonl"), key=lambda r: r.seq)
    assert [(r.actor, r.tool) for r in rows] == [
        ("orchestrator", "delegate"),
        ("sub-1", "create_charge"),
        ("sub-1", "finish_report"),
        ("orchestrator", "finish"),
    ]
    assert len(effects(tmp_path)) == 1
    assert (tmp_path / "ep" / STREAM_FILE).exists()
    assert result.cost == pytest.approx(0.05 + 0.002)  # harness + two subagent model calls


async def test_m4_repeats_and_keys_work_under_the_harness(tmp_path, monkeypatch) -> None:
    # respawn: the report of sub-1 is lost, the stand runs sub-2; the intent key stops the repeat.
    subagent = [CHARGE, report(), CHARGE, report()]
    plan = [[DELEGATE], [FINISH]]
    result = await run(tmp_path, monkeypatch, plan, subagent, fault="respawn", key="intent")

    assert result.delegations == 2
    assert len(effects(tmp_path)) == 1


async def test_finish_next_to_a_running_call_is_rejected(tmp_path, monkeypatch) -> None:
    plan = [[DELEGATE, FINISH], [FINISH]]
    result = await run(tmp_path, monkeypatch, plan, [CHARGE, report()])

    rows = CallJournal.read(tmp_path / "ep" / "calls.jsonl")
    assert [r.error_code for r in rows if r.tool == "finish"] == ["finish_not_alone", None]
    assert result.stop_reason == "finish"


async def test_text_answer_without_finish(tmp_path, monkeypatch) -> None:
    result = await run(tmp_path, monkeypatch, ["I paid it."], [])

    assert result.stop_reason == "text_only"
    assert result.final_text == "I paid it."
    assert result.finish_args is None


async def test_tools_outside_the_cell_are_refused(tmp_path, monkeypatch) -> None:
    # Calibration: the orchestrator has no bank tools, only delegate and finish.
    plan = [[("create_charge", CHARGE[0][1])], [FINISH]]
    await run(tmp_path, monkeypatch, plan, [])

    rows = CallJournal.read(tmp_path / "ep" / "calls.jsonl")
    assert [(r.tool, r.error_code) for r in rows][0] == ("create_charge", "unknown_tool")
    assert effects(tmp_path) == []
