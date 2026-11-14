import json

import pytest
from helpers.episode import CHARGE, DELEGATE, delegate_result, effects, finish, report, run
from helpers.fake_model import FakeModel

from handoff_bench.agents.delegation import ORCHESTRATOR_SYSTEM_PROMPT
from handoff_bench.channels.render import HEADER

pytestmark = pytest.mark.anyio


def seen_by_orchestrator(model: FakeModel) -> list[str]:
    """`result` of every delegate call, from the orchestrator's last request."""
    last = next(
        r for r in reversed(model.requests) if r[0]["content"] == ORCHESTRATOR_SYSTEM_PROMPT
    )
    return [
        json.loads(m["content"])["result"]
        for m in last
        if m["role"] == "tool" and m["content"].endswith('"status": "completed"}')
    ]


async def test_code_channel_pre_says_there_was_no_charge(tmp_path) -> None:
    model, _ = await run(
        tmp_path, [DELEGATE, CHARGE, finish("unknown")], fault="pre", channel="code"
    )

    assert model.requests[-1][-1]["content"] == delegate_result(
        f"{HEADER}\n- no create_charge calls"
    )


async def test_code_channel_post_shows_the_charge_text_channel_does_not(tmp_path) -> None:
    script = [DELEGATE, CHARGE, report(), finish()]
    code, _ = await run(tmp_path / "code", script, fault="post", channel="code")
    text, _ = await run(tmp_path / "text", script, fault="post", channel="text")

    (charge,) = effects(tmp_path / "code")
    assert code.requests[-1][-1]["content"] == delegate_result(
        f"{HEADER}\n- step 1: create_charge INV-12: success, charge {charge.charge_id}"
    )
    assert text.requests[-1][-1]["content"] == delegate_result("")


async def test_statement_covers_only_this_subagent(tmp_path) -> None:
    script = [DELEGATE, CHARGE, DELEGATE, CHARGE, report(), finish()]
    model, _ = await run(tmp_path, script, fault="pre", channel="code")

    first, second = seen_by_orchestrator(model)
    assert first == f"{HEADER}\n- no create_charge calls"
    assert second.startswith(f"{HEADER}\n- step 1: create_charge INV-12: success")
    assert second.count("create_charge") == 1


@pytest.mark.parametrize("fault", ["timeout_lost", "timeout_committed"])
async def test_struct_cells_8_and_9_pass_the_form(tmp_path, fault) -> None:
    form = report("unknown", "Timed out.", uncertain=["charge INV-12"])
    model, _ = await run(
        tmp_path, [DELEGATE, CHARGE, form, finish("unknown")], fault=fault, channel="struct"
    )

    assert json.loads(model.requests[-1][-1]["content"])["result"] == (
        "status: unknown\ndone: none\nuncertain:\n- charge INV-12\ntext: Timed out."
    )
