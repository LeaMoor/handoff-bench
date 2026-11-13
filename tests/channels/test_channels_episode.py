import json

import pytest
from helpers.episode import CHARGE, DELEGATE, delegate_result, effects, finish, report, run

from handoff_bench.channels.render import HEADER

pytestmark = pytest.mark.anyio


def seen_by_orchestrator(model) -> list[str]:
    """`result` of every delegate call, as the orchestrator's model received it."""
    out = []
    for request in model.requests:
        for msg in request:
            if msg.get("role") == "tool" and '"status": "completed"' in msg["content"]:
                out.append(json.loads(msg["content"])["result"])
    return list(dict.fromkeys(out))


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
        f"{HEADER}\n- step 2: create_charge INV-12: success, charge {charge.charge_id}"
    )
    assert text.requests[-1][-1]["content"] == delegate_result("")


async def test_statement_covers_only_this_subagent(tmp_path) -> None:
    script = [DELEGATE, CHARGE, DELEGATE, CHARGE, report(), finish()]
    model, _ = await run(tmp_path, script, fault="pre", channel="code")

    first, second = seen_by_orchestrator(model)
    assert first == f"{HEADER}\n- no create_charge calls"
    assert second.startswith(f"{HEADER}\n- step 4: create_charge INV-12: success")
    assert second.count("create_charge") == 1
