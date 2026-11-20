"""M4: the harness repeats the work; who sets the idempotency key decides the duplicate."""

import json

import pytest
from helpers.episode import DELEGATE, effects, finish, report, run

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import ORCHESTRATOR_SYSTEM_PROMPT
from handoff_bench.keys import ORCHESTRATOR_KEY_PROMPT

pytestmark = pytest.mark.anyio


def charge(key: str | None = None, amount: int = 12000):
    args = {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": amount}
    if key:
        args["idempotency_key"] = key
    return [("create_charge", args)]


def two_runs(first, second):
    """The orchestrator delegates once; the harness runs two subagents."""
    return [DELEGATE, first, report(), second, report(), finish()]


@pytest.mark.parametrize(
    ("fault", "key", "charges"),
    [
        ("respawn", "subagent", 2),  # each subagent makes up its own key
        ("respawn", "none", 2),
        ("respawn", "task", 2),  # a respawn is a new task: a new key
        ("redeliver", "task", 1),  # a double delivery is the same task: the same key
        ("respawn", "intent", 1),
        ("respawn", "pinned", 1),
    ],
)
async def test_who_sets_the_key_decides_the_duplicate(tmp_path, fault, key, charges) -> None:
    script = two_runs(charge("sub-1-key"), charge("sub-2-key"))
    _, result = await run(tmp_path, script, fault=fault, key=key)

    assert len(effects(tmp_path)) == charges
    assert result.delegations == 2


async def test_intent_key_refuses_other_params_pinned_replays(tmp_path) -> None:
    script = two_runs(charge(), charge(amount=11000))
    await run(tmp_path / "intent", script, fault="respawn", key="intent")
    await run(tmp_path / "pinned", script, fault="respawn", key="pinned")

    def second(path):
        rows = CallJournal.read(path / "ep" / "calls.jsonl")
        return next(r for r in rows if r.actor == "sub-2" and r.tool == "create_charge")

    assert (second(tmp_path / "intent").outcome, second(tmp_path / "intent").error_code) == (
        "error",
        "idempotency_error",
    )
    assert second(tmp_path / "pinned").outcome == "ok"
    assert len(effects(tmp_path / "intent")) == len(effects(tmp_path / "pinned")) == 1


async def test_no_key_hides_the_field_and_strips_it(tmp_path) -> None:
    model, _ = await run(
        tmp_path, two_runs(charge("k"), charge("k")), fault="redeliver", key="none"
    )

    sub_tools = model.tools[1]  # the first subagent's request
    schema = next(t for t in sub_tools if t["function"]["name"] == "create_charge")
    assert "idempotency_key" not in schema["function"]["parameters"]["properties"]
    assert [e.idempotency_key for e in effects(tmp_path)] == [None, None]


async def test_orchestrator_key_source_asks_for_a_key_in_the_goal(tmp_path) -> None:
    model, _ = await run(
        tmp_path, two_runs(charge(), charge()), fault="respawn", key="orchestrator"
    )

    assert model.requests[0][0]["content"] == ORCHESTRATOR_SYSTEM_PROMPT + ORCHESTRATOR_KEY_PROMPT


async def test_with_the_charge_hidden_only_the_key_prevents_a_repeat(tmp_path) -> None:
    look = [("get_invoice", {"invoice_id": "INV-12"})]
    script = [DELEGATE, charge("k"), report(), look, report(), finish()]
    await run(tmp_path, script, fault="respawn", key="intent", delay="long")

    rows = CallJournal.read(tmp_path / "ep" / "calls.jsonl")
    seen = next(r.result for r in rows if r.actor == "sub-2" and r.tool == "get_invoice")
    assert seen["status"] == "open"


async def test_statement_with_key_shows_the_subagents_key(tmp_path) -> None:
    model, _ = await run(
        tmp_path,
        [DELEGATE, charge("my-key-1"), report(), finish()],
        fault="post",
        channel="code_key",
        delay="long",
    )

    seen = json.loads(model.requests[-1][-1]["content"])["result"]
    assert "create_charge INV-12 (idempotency key my-key-1): success" in seen
