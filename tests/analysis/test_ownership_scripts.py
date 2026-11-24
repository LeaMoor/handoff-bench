"""Check the analysis entry points with historical and current M8 cell IDs."""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from handoff_bench.billing import BankConfig, BillingService, EffectJournal
from handoff_bench.billing.models import EffectRow
from handoff_bench.worlds import generate

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def test_grid_ignores_ownership_rows_with_hedged_cell_ids(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    row = {
        "cell": 70,
        "episode_id": "c70-w1-t2-s1-r0",
        "model": "anthropic/claude-haiku-5.5",
        "agent": "delegated",
        "rules": True,
        "fault": "timeout_committed",
        "claim": "done",
        "effect": "once",
        "self_checked": False,
    }
    hedged = dict(row, block="main", channel="code_hedged", outcome="eos", correct=True)
    historical_m8 = dict(row, block="ownership", channel="text", outcome="duplicate", correct=False)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    pd.DataFrame([hedged]).to_csv(baseline / "results.csv", index=False)
    pd.DataFrame([historical_m8, hedged]).to_csv(run / "results.csv", index=False)

    argv = [sys.executable, str(SCRIPTS / "grid.py")]
    expected = subprocess.run(
        [*argv, str(baseline)], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    result = subprocess.run(
        [*argv, str(run)], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    assert "code_hedged" in result.stdout
    assert result.stdout == expected.stdout


@pytest.mark.parametrize(("cell", "template"), [(70, 2), (74, 4)])
def test_regrade_uses_the_m8_world_for_historical_and_current_cells(
    tmp_path: Path, cell: int, template: int
) -> None:
    run = tmp_path / "runs" / "pilot"
    episode = run / f"c{cell}-w1-t{template}-s1-r0"
    episode.mkdir(parents=True)
    world = generate(1, 1, 4)
    missing = world.targets[-1]
    (episode / "config.json").write_text(
        json.dumps(
            {
                "episode": {
                    "cell": cell,
                    "block": "ownership",
                    "world": 1,
                    "seed": 1,
                    "template": template,
                },
                "worklists": {"work-a": world.targets[:2], "work-b": world.targets[1:]},
            }
        )
    )
    (episode / "trace.json").write_text(
        json.dumps(
            {
                "orchestrator": {
                    "stop_reason": "finish",
                    "finish_args": {"status": "done", "done": world.targets, "message": ""},
                    "messages": [],
                },
                "subagents": [],
                "parallel_overlap": True,
                "tasks": {"sub-1": "work-a", "sub-2": "work-b"},
            }
        )
    )
    (episode / "calls.jsonl").touch()
    journal = EffectJournal(episode / "effects.jsonl")
    if template == 2:
        with BillingService(
            episode_id=episode.name,
            config=BankConfig(),
            journal=journal,
            db_path=episode / "bank.sqlite",
        ) as bank:
            bank.add_invoices(world.invoices)
    for step, invoice in enumerate(world.invoices[:2], start=1):
        journal.write(
            EffectRow(
                episode_id=episode.name,
                step=step,
                invoice_id=invoice.invoice_id,
                customer_id=invoice.customer_id,
                amount=invoice.amount,
                charge_id=f"charge-{step}",
                idempotency_key=None,
                actor="sub-1",
                reported_as="succeeded",
            )
        )
    pd.DataFrame(
        [{"episode_id": episode.name, "outcome": "eos", "first_action": "finish_done"}]
    ).to_csv(run / "results.csv", index=False)

    subprocess.run(
        [sys.executable, str(SCRIPTS / "regrade.py"), str(run)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (row,) = pd.read_csv(tmp_path / "runs/night-regraded.csv").to_dict(orient="records")
    result = json.loads(row["ownership"])
    assert row["cell"] == cell
    assert row["new_outcome"] == "false_done"
    assert row["effect"] == "skip"
    assert result["skipped_invoices"] == [missing]
    assert result["records"][missing]["expected_amount"] == world.focal.amount
    assert result["off_target_charges"] == 0
