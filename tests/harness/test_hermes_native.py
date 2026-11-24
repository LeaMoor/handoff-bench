"""Hermes' own subagents (cells 94-101): real Hermes in its own interpreter, a scripted model.

The model is `fake_openai.FakeOpenAI`; Hermes, its `delegate_task`, our plugin, the stand and the
bank are real. Skipped when Hermes is not installed here.
"""

import glob
import json
from pathlib import Path

import pytest
from helpers.episode import DEMO_WORLD, GOAL, effects
from helpers.fake_openai import FakeOpenAI

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.cells import CELLS, find_cell
from handoff_bench.harness import hermes_plugin as plugin
from handoff_bench.harness.hermes import HermesSettings
from handoff_bench.harness.hermes_native import run_hermes_native_episode, with_reports
from handoff_bench.runner.episode import EpisodeConfig

pytestmark = pytest.mark.anyio

AGENT_ROOT = Path.home() / ".hermes" / "hermes-agent"
PYTHONS = sorted(glob.glob(str(Path.home() / ".hermes/tools/python-3*/bin/python3")))
needs_hermes = pytest.mark.skipif(
    not (AGENT_ROOT / "run_agent.py").exists() or not PYTHONS, reason="Hermes is not installed"
)

ARGS = {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000}
DELEGATE = {"calls": [["delegate_task", {"tasks": [{"goal": GOAL}]}]]}
FINISH = {
    "calls": [["finish", {"status": "done", "done": [], "uncertain": [], "message": "Paid."}]]
}


def charge(key: str | None = None) -> dict:
    return {"calls": [["create_charge", ARGS | ({"idempotency_key": key} if key else {})]]}


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hermes' auxiliary calls must not find a real provider: only the fake endpoint answers.
    for var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.delenv(var, raising=False)


async def run(
    tmp_path: Path,
    plan: dict,
    *,
    fault: str = "timeout_committed",
    key: str = "none",
    subagent_model: str | None = None,
):
    cell = find_cell(fault, "native", delay="long", rules=False, key=key, block="hermes_native")
    with FakeOpenAI(plan) as fake:
        settings = HermesSettings(
            model="fake/model",
            provider="custom",
            base_url=fake.url,
            agent_root=str(AGENT_ROOT),
            subagent_model=subagent_model,
            timeout_s=120,
            finish_grace_s=20,
        )
        config = EpisodeConfig(cell=cell.number, template=1, seed=0, orchestrator=settings)
        result = await run_hermes_native_episode(DEMO_WORLD, config, episode_dir=tmp_path / "ep")
    return result, fake


def calls(tmp_path: Path) -> list[tuple]:
    rows = sorted(CallJournal.read(tmp_path / "ep" / "calls.jsonl"), key=lambda r: r.seq)
    return [(r.actor, r.tool, r.outcome) for r in rows]


@needs_hermes
@pytest.mark.parametrize(
    ("key", "keys", "charges"),
    [
        ("none", ("k1", "k1"), 2),  # the stand strips the children's keys
        ("subagent", ("k1", "k2"), 2),  # each child makes up its own
        ("subagent", ("k1", "k1"), 1),
        ("task", ("k1", "k1"), 2),  # a key per child
        ("intent", (None, None), 1),  # a key per payment
    ],
)
async def test_parent_repeats_and_the_stand_key_decides(tmp_path, key, keys, charges) -> None:
    plan = {
        "parent": [DELEGATE, DELEGATE, FINISH],
        "children": [
            [charge(keys[0]), {"text": "Timed out."}],
            [charge(keys[1]), {"text": "Paid."}],
        ],
    }
    result, fake = await run(tmp_path, plan, key=key)

    assert result.stop_reason == "finish", result.error
    assert result.delegations == 2
    assert result.fault_fired
    assert len(effects(tmp_path)) == charges
    assert calls(tmp_path) == [
        ("orchestrator", "delegate", "ok"),
        ("sub-1", "create_charge", "timeout"),
        ("orchestrator", "delegate", "ok"),
        ("sub-2", "create_charge", "ok"),
        ("orchestrator", "finish", "ok"),
    ]
    rows = sorted(CallJournal.read(tmp_path / "ep" / "calls.jsonl"), key=lambda r: r.seq)
    assert rows[0].args == {"goal": GOAL}
    assert "Timed out." in str(rows[0].result)  # the child's report, as Hermes returned it
    trace = json.loads((tmp_path / "ep" / "trace.json").read_text())
    assert [s["actor"] for s in trace["subagents"]] == ["sub-1", "sub-2"]
    assert trace["hermes_built_deps"] is False  # it borrowed the user's own dependencies
    assert trace["orchestrator"]["messages"][1]["content"] == DEMO_WORLD.task
    # The report reached the parent through Hermes: its second request carries it.
    second = [s for s in fake.seen if s["role"] == "parent"][1]
    assert any("Timed out." in str(m.get("content")) for m in second["messages"])


@needs_hermes
async def test_tools_are_split_as_in_claude_code_b(tmp_path) -> None:
    plan = {"parent": [DELEGATE, FINISH], "children": [[charge(), {"text": "Paid."}]]}
    _, fake = await run(tmp_path, plan, fault="timeout_lost")

    parent = {tuple(s["tools"]) for s in fake.seen if s["role"] == "parent"}
    child = {tuple(s["tools"]) for s in fake.seen if s["role"].startswith("child")}
    assert parent == {("delegate_task", "finish", "get_invoice", "list_charges")}
    assert child == {("create_charge", "get_invoice", "list_charges")}


@needs_hermes
async def test_children_run_on_their_own_model(tmp_path) -> None:
    plan = {"parent": [DELEGATE, FINISH], "children": [[charge(), {"text": "Paid."}]]}
    result, fake = await run(tmp_path, plan, subagent_model="fake/child")

    assert result.stop_reason == "finish", result.error
    models = {(s["role"], s["model"]) for s in fake.seen if s["role"] != "aux"}
    assert models == {("parent", "fake/model"), ("child-1", "fake/child")}
    hooks = [json.loads(line) for line in (tmp_path / "ep" / "hermes_hooks.jsonl").open()]
    logged = {(h["actor"], h["model"]) for h in hooks if h["kind"] == "api_request"}
    assert logged == {("orchestrator", "fake/model"), ("sub-1", "fake/child")}


@needs_hermes
async def test_parent_cannot_pay_itself(tmp_path) -> None:
    plan = {"parent": [charge(), FINISH], "children": []}
    result, _ = await run(tmp_path, plan)

    assert result.stop_reason == "finish", result.error
    assert effects(tmp_path) == []
    assert all(tool != "create_charge" for _, tool, _ in calls(tmp_path))


@needs_hermes
async def test_continue_is_refused(tmp_path) -> None:
    resume = {
        "calls": [["delegate_task", {"action": "continue", "subagent_id": "x", "message": "go"}]]
    }
    plan = {"parent": [resume, FINISH], "children": []}
    result, _ = await run(tmp_path, plan)

    assert result.stop_reason == "finish", result.error
    assert result.delegations == 0
    hooks = [json.loads(line) for line in (tmp_path / "ep" / "hermes_hooks.jsonl").open()]
    blocked = [h for h in hooks if h["kind"] == "pre_tool_call" and h["decision"]]
    assert [h["decision"]["message"] for h in blocked] == [plugin.CONTINUE_REFUSED]


def test_plugin_names_children_in_start_order(monkeypatch) -> None:
    posted = []
    monkeypatch.setattr(plugin, "call_stand", lambda *a: posted.append(a) or "{}")
    plugin.configure({"orchestrator": "orchestrator", "delegate_tool": "delegate"})
    plugin.on_subagent_start(child_session_id="s-a", child_goal="g1")
    plugin.on_subagent_start(child_session_id="s-b", child_goal="g2")

    assert plugin.actor_of("s-b") == "sub-2"
    assert plugin.actor_of("s-a") == "sub-1"
    assert plugin.actor_of("parent-session") == "orchestrator"
    assert posted == [
        ("orchestrator", "delegate", {"goal": "g1"}),
        ("orchestrator", "delegate", {"goal": "g2"}),
    ]
    assert plugin.on_pre_tool_call("delegate_task", {"tasks": [{"goal": "g"}]}, "p") is None
    refused = plugin.on_pre_tool_call("delegate_task", {"action": "continue"}, "p")
    assert refused == {"action": "block", "message": plugin.CONTINUE_REFUSED}


def test_delegate_rows_get_their_childs_report() -> None:
    from handoff_bench.agents.call_journal import CallRow

    def row(seq, actor, tool):
        return CallRow(episode_id="e", seq=seq, actor=actor, tool=tool, args={}, outcome="ok")

    rows = [
        row(1, "orchestrator", "delegate"),
        row(2, "sub-1", "create_charge"),
        row(3, "orchestrator", "delegate"),
    ]
    hooks = [
        {"kind": "subagent_stop", "actor": "sub-2", "summary": "B"},
        {"kind": "subagent_stop", "actor": "sub-1", "summary": "A"},
    ]
    assert [r.result for r in with_reports(rows, hooks)] == ["A", None, "B"]


def test_hermes_native_cells() -> None:
    cells = [c for c in CELLS.values() if c.block == "hermes_native"]
    assert [c.number for c in cells] == list(range(94, 102))
    assert {(c.fault, c.key) for c in cells} == {
        (f, k)
        for f in ("timeout_lost", "timeout_committed")
        for k in ("none", "subagent", "task", "intent")
    }
    assert {c.channel for c in cells} == {"native"}
