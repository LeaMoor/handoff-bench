import json
from pathlib import Path

import pytest
from helpers.episode import finish
from helpers.fake_model import FakeModel

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.runner.episode import EpisodeConfig
from handoff_bench.runner.run import run_one
from handoff_bench.worlds import generate

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("cell", "charge_count", "review_error"),
    [
        (95, 0, None),
        (96, 1, None),
        (97, 0, "review_unavailable"),
        (98, 1, "review_unavailable"),
    ],
)
async def test_single_workflow_preserves_hidden_bank_outcome(
    tmp_path: Path, cell: int, charge_count: int, review_error: str | None
) -> None:
    world = generate(1, 1, 1)
    charge = {
        "invoice_id": world.focal_invoice,
        "customer_id": world.customer_id,
        "amount": world.focal.amount,
    }
    model = FakeModel(
        [
            [("create_charge", charge)],
            [("review_workflow", {})],
            [("review_workflow", {})],
            [("list_charges", {"invoice_id": world.focal_invoice})],
            finish("unknown"),
        ]
    )
    config = EpisodeConfig(cell=cell, template=1, seed=1)
    row = await run_one(config, model, run_dir=tmp_path, run_id="test")
    calls = CallJournal.read(tmp_path / config.episode_id / "calls.jsonl")
    review = [call for call in calls if call.tool == "review_workflow"]
    assert all(call.actor == "orchestrator" for call in calls)
    assert [call.error_code for call in review] == [None, review_error]
    assert [call.result for call in calls if call.tool == "list_charges"] == [[]]
    assert row.fault_fired and row.focal_charges == charge_count
    trace = json.loads((tmp_path / config.episode_id / "trace.json").read_text())
    assert trace["subagents"] == []
