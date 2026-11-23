"""Native cells: Claude Code's own subagents, played by the fake `claude` with our hook applied."""

import json
from pathlib import Path

import pytest
from helpers.episode import DEMO_WORLD, GOAL, effects
from helpers.harness import FINISH, fake_binary

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.cells import CELLS, find_cell
from handoff_bench.harness.claude_code import HarnessSettings
from handoff_bench.harness.hook import HookPolicy, decide
from handoff_bench.harness.native import run_native_episode
from handoff_bench.keys import intent_key
from handoff_bench.runner.episode import EpisodeConfig

pytestmark = pytest.mark.anyio

ARGS = {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000}
POLICY = HookPolicy(
    log=Path("unused"), parent_bank_tools=("get_invoice", "list_charges"), key="none"
)


def pre(tool: str, args: dict, agent: str | None = None) -> dict:
    event = {"hook_event_name": "PreToolUse", "tool_name": f"mcp__bank__{tool}", "tool_input": args}
    return event | ({"agent_id": agent} if agent else {})


def test_hook_keeps_payment_from_the_orchestrator() -> None:
    out = decide(pre("create_charge", ARGS), POLICY)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert decide(pre("list_charges", {"invoice_id": "INV-12"}), POLICY) == {}
    assert decide({"hook_event_name": "SubagentStop"}, POLICY) == {}


@pytest.mark.parametrize(
    ("key", "sent"),
    [
        ("none", None),
        ("subagent", "mine"),
        ("task", "task:a1"),
        ("intent", intent_key(ARGS)),
    ],
)
def test_hook_sets_the_key_of_a_subagent_charge(key, sent) -> None:
    policy = POLICY.model_copy(update={"key": key})
    out = decide(pre("create_charge", ARGS | {"idempotency_key": "mine"}, agent="a1"), policy)
    args = out.get("hookSpecificOutput", {}).get("updatedInput", ARGS | {"idempotency_key": "mine"})
    assert args.get("idempotency_key") == sent


def agent(report: str, key: str | None = None) -> list:
    charge = ARGS | ({"idempotency_key": key} if key else {})
    return ["Agent", {"prompt": GOAL, "turns": [[["create_charge", charge]]], "report": report}]


async def run(tmp_path, monkeypatch, plan, *, fault="timeout_committed", key="none"):
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(plan))
    settings = HarnessSettings(model="fake", binary=fake_binary(tmp_path), finish_grace_s=5)
    cell = find_cell(fault, "native", delay="long", rules=False, key=key)
    config = EpisodeConfig(cell=cell.number, template=1, seed=0, orchestrator=settings)
    return await run_native_episode(DEMO_WORLD, config, episode_dir=tmp_path / "ep")


def calls(tmp_path: Path) -> list[tuple]:
    rows = sorted(CallJournal.read(tmp_path / "ep" / "calls.jsonl"), key=lambda r: r.seq)
    return [(r.actor, r.tool, r.outcome) for r in rows]


@pytest.mark.parametrize(
    ("key", "keys", "charges"),
    [
        ("none", ("k1", "k1"), 2),  # the hook strips the subagents' keys
        ("subagent", ("k1", "k2"), 2),  # each subagent makes up its own
        ("subagent", ("k1", "k1"), 1),
        ("task", ("k1", "k1"), 2),  # a key per subagent
        ("intent", (None, None), 1),  # a key per payment
    ],
)
async def test_orchestrator_repeats_and_the_hook_key_decides(
    tmp_path, monkeypatch, key, keys, charges
) -> None:
    plan = [[agent("Timed out.", keys[0])], [agent("Paid.", keys[1])], [FINISH]]
    result = await run(tmp_path, monkeypatch, plan, key=key)

    assert result.stop_reason == "finish"
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
    trace = json.loads((tmp_path / "ep" / "trace.json").read_text())
    assert [s["actor"] for s in trace["subagents"]] == ["sub-1", "sub-2"]
    assert trace["orchestrator"]["messages"][1]["content"] == DEMO_WORLD.task


async def test_orchestrator_cannot_pay_itself(tmp_path, monkeypatch) -> None:
    plan = [[["create_charge", ARGS]], [["list_charges", {"invoice_id": "INV-12"}]], [FINISH]]
    await run(tmp_path, monkeypatch, plan)

    assert calls(tmp_path)[:2] == [
        ("orchestrator", "create_charge", "rejected"),
        ("orchestrator", "list_charges", "ok"),
    ]
    assert effects(tmp_path) == []


def test_native_cells() -> None:
    native = [c for c in CELLS.values() if c.block == "native"]
    assert [c.number for c in native] == list(range(86, 94))
    assert {(c.fault, c.key) for c in native} == {
        (f, k)
        for f in ("timeout_lost", "timeout_committed")
        for k in ("none", "subagent", "task", "intent")
    }
