import pytest
from helpers.episode import CHARGE, DELEGATE, DEMO_WORLD, LIST, finish, report, run
from helpers.fake_model import FakeModel

from handoff_bench.agents.delegation import SUBAGENT_RULES
from handoff_bench.agents.model_client import ModelSettings
from handoff_bench.analysis.parity import episode_roles, role_rows, served_mismatches
from handoff_bench.cells import find_cell
from handoff_bench.runner.episode import EpisodeConfig, run_episode

pytestmark = pytest.mark.anyio

SCRIPT = [DELEGATE, CHARGE, LIST, report("done", "Paid INV-12."), finish()]


async def test_one_row_per_actor_with_what_its_model_got(tmp_path) -> None:
    await run(tmp_path, SCRIPT, fault="timeout_committed")

    rows = {r["role"]: r for r in episode_roles(tmp_path / "ep")}
    assert set(rows) == {"orchestrator", "subagent"}
    orch, sub = rows["orchestrator"], rows["subagent"]
    assert orch["tools"] == "delegate,finish,get_invoice,list_charges"
    assert sub["tools"] == "create_charge,finish_report,get_invoice,list_charges"
    assert orch["served_model"] == sub["served_model"]  # the raw API answer names it; fakes do not
    assert sub["stand_rules"] and not orch["stand_rules"]
    # The goal the orchestrator wrote says nothing about acting once; the stand's rules do.
    assert not sub["asks_once"]
    assert (orch["max_steps"], sub["max_steps"]) == (15, 10)
    assert sub["stop_reason"] == "finish"


async def test_without_rules_the_goal_is_the_orchestrators_text_only(tmp_path) -> None:
    await run(tmp_path, SCRIPT, fault="timeout_committed", rules=False)

    sub = next(r for r in episode_roles(tmp_path / "ep") if r["role"] == "subagent")
    assert not sub["stand_rules"]
    assert SUBAGENT_RULES not in sub["system_prompt"]


async def test_same_served_model_is_no_mismatch(tmp_path) -> None:
    await run(tmp_path / "run", SCRIPT, fault="timeout_committed")

    df = role_rows([tmp_path / "run"])
    assert served_mismatches(df).empty


async def test_each_role_is_labelled_with_its_own_configured_model(tmp_path) -> None:
    config = EpisodeConfig(
        cell=find_cell("timeout_committed", "text").number,
        template=1,
        seed=0,
        model=ModelSettings(model="fake/strong", provider="fake"),
        subagent_model=ModelSettings(model="fake/weak", provider="fake"),
    )
    episode = tmp_path / "run" / "ep"
    await run_episode(
        FakeModel([DELEGATE, finish()]),
        DEMO_WORLD,
        config,
        episode_dir=episode,
        subagent_model=FakeModel([CHARGE, report()]),
    )

    labels = {r["role"]: r["configured_model"] for r in episode_roles(episode)}
    assert labels == {"orchestrator": "fake/strong", "subagent": "fake/weak"}
    assert served_mismatches(role_rows([tmp_path / "run"])).empty


async def test_an_unfinished_episode_is_skipped(tmp_path) -> None:
    await run(tmp_path / "run", SCRIPT, fault="timeout_committed")
    (tmp_path / "run" / "ep" / "trace.json").unlink()

    assert role_rows([tmp_path / "run"]).empty
