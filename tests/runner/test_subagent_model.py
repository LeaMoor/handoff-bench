import json
from pathlib import Path

import pytest
from helpers.episode import CHARGE, DELEGATE, DEMO_WORLD, finish, report
from helpers.fake_model import FakeModel

from handoff_bench.agents.delegation import SUBAGENT_SYSTEM_PROMPT
from handoff_bench.agents.model_client import ModelSettings
from handoff_bench.cells import find_cell
from handoff_bench.runner.batch import BatchConfig, run_batch
from handoff_bench.runner.episode import EpisodeConfig, run_episode

pytestmark = pytest.mark.anyio

ORCH = ModelSettings(model="fake/strong", provider="fake")
SUB = ModelSettings(model="fake/weak", provider="fake")


async def test_subagents_run_on_their_own_model(tmp_path: Path) -> None:
    orchestrator, subagent = FakeModel([DELEGATE, finish()]), FakeModel([CHARGE, report()])
    cell = find_cell("timeout_committed", "text")
    config = EpisodeConfig(cell=cell.number, template=1, seed=0, model=ORCH, subagent_model=SUB)

    result = await run_episode(
        orchestrator, DEMO_WORLD, config, episode_dir=tmp_path, subagent_model=subagent
    )

    assert result.stop_reason == "finish"
    assert [r[0]["content"] for r in subagent.requests] == [SUBAGENT_SYSTEM_PROMPT] * 2
    assert len(orchestrator.requests) == 2
    assert all(r[0]["content"] != SUBAGENT_SYSTEM_PROMPT for r in orchestrator.requests)
    saved = json.loads((tmp_path / "config.json").read_text())["episode"]
    assert saved["subagent_model"]["model"] == "fake/weak"


async def test_without_a_subagent_model_both_roles_share_one(tmp_path: Path) -> None:
    model = FakeModel([DELEGATE, CHARGE, report(), finish()])
    config = EpisodeConfig(cell=find_cell("none", "text").number, template=1, seed=0)

    await run_episode(model, DEMO_WORLD, config, episode_dir=tmp_path)

    assert len(model.requests) == 4


async def test_batch_passes_the_subagent_model_on(tmp_path: Path) -> None:
    cell = find_cell("timeout_committed", "text").number
    cfg = BatchConfig(
        model=ORCH, subagent_model=SUB, cells=[cell], templates=[1], seeds=[1], budget_usd=1
    )
    assert {e.subagent_model for e in cfg.episodes("abc")} == {SUB}

    orchestrator, subagent = FakeModel([DELEGATE, finish()]), FakeModel([CHARGE, report()])
    summary = await run_batch(
        cfg,
        lambda _: orchestrator,
        run_dir=tmp_path,
        run_id="t",
        commit="abc",
        subagent_model=subagent,
    )
    assert (summary.finished, summary.crashed) == (1, 0)
    assert len(subagent.requests) == 2
