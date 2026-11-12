"""Задача 1, критерий: одна команда → агент заплатил → в журнале одно списание.

Здесь — на фальшивой модели, но с настоящим банком по HTTP и настоящими журналами на диске.
"""

import json

import pytest
from helpers.fake_model import FakeModel

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.billing import EffectJournal
from handoff_bench.runner.skeleton import run_skeleton

pytestmark = pytest.mark.anyio

PAYS_INVOICE = [
    [("get_invoice", {"invoice_id": "INV-12"})],
    [("create_charge", {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000})],
    [("finish", {"status": "done", "message": "Paid INV-12."})],
]


async def test_agent_pays_invoice_one_charge(tmp_path) -> None:
    ep = tmp_path / "ep"
    result = await run_skeleton(FakeModel(PAYS_INVOICE), episode_dir=ep, episode_id="ep")

    assert result.stop_reason == "finish"
    assert result.focal_charges == 1
    assert result.finish_args == {"status": "done", "message": "Paid INV-12."}
    assert result.cost == pytest.approx(0.003)

    (effect,) = EffectJournal.read(ep / "effects.jsonl")
    assert effect.actor == "agent"
    calls = CallJournal.read(ep / "calls.jsonl")
    assert [c.tool for c in calls] == ["get_invoice", "create_charge", "finish"]
    assert calls[1].result["charge_id"] == effect.charge_id
    assert len((ep / "model_calls.jsonl").read_text().splitlines()) == 3
    trace = json.loads((ep / "trace.json").read_text())
    assert trace["messages"][0]["role"] == "system"
    assert json.loads((ep / "summary.json").read_text())["focal_charges"] == 1


async def test_agent_sees_bank_tools_and_finish(tmp_path) -> None:
    model = FakeModel([[("finish", {"status": "unknown", "message": "?"})]])
    await run_skeleton(model, episode_dir=tmp_path / "ep", episode_id="ep")
    # Какие инструменты получила модель — видно в логе вызовов модели.
    log = [
        json.loads(line)
        for line in (tmp_path / "ep" / "model_calls.jsonl").read_text().splitlines()
    ]
    assert set(log[0]["tools"]) == {"get_invoice", "create_charge", "list_charges", "finish"}


async def test_bad_finish_status_is_rejected_and_agent_continues(tmp_path) -> None:
    model = FakeModel(
        [
            [("finish", {"status": "great", "message": "?"})],
            [("finish", {"status": "failed", "message": "could not pay"})],
        ]
    )
    result = await run_skeleton(model, episode_dir=tmp_path / "ep", episode_id="ep")

    assert (result.steps, result.finish_args["status"]) == (2, "failed")
    assert result.focal_charges == 0
