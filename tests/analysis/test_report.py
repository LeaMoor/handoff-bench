import runpy
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from handoff_bench.analysis import report
from handoff_bench.analysis.synthetic import synthetic_results
from handoff_bench.cells import CELLS
from handoff_bench.cli import app
from handoff_bench.runner.results import ResultRow, ResultsCsv

NOTEBOOK = Path(__file__).parents[2] / "notebooks" / "week2_analysis.py"


def ep(cell: int, outcome: str, seed: int = 1, repeat: int = 0, **extra) -> dict:
    return {
        "cell": cell,
        "channel": CELLS[cell].channel,
        "outcome": outcome,
        "correct": outcome in ("eos", "honest_unknown"),
        "effect": {"skip": "skip", "false_done": "skip", "duplicate": "duplicate"}.get(
            outcome, "once"
        ),
        "false_done": False,
        "self_checked": False,
        "first_action": "finish_done",
        "retries": 0,
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "cost": 0.01,
        "world": 1,
        "template": 1,
        "seed": seed,
        "repeat": repeat,
        "fault": CELLS[cell].fault,
        "fault_fired": CELLS[cell].fault != "none",
        **extra,
    }


def frame(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def test_calibration_pair_error_and_unknown_pairs() -> None:
    df = frame(
        ep(2, "skip", seed=1),
        ep(3, "duplicate", seed=1),
        ep(2, "eos", seed=2),
        ep(3, "eos", seed=2),
        ep(2, "honest_unknown", seed=3),
        ep(3, "duplicate", seed=3),
        ep(2, "false_done", seed=4),
        ep(3, "invalid", seed=4),
        ep(2, "eos", seed=5, fault_fired=False),
        ep(3, "duplicate", seed=5),
    )
    pairs = report.calibration_pairs(df)
    summary = report.calibration_summary(df).loc[("text", "short")]

    assert sorted(pairs["error"]) == [0, 1, 2]
    assert (summary["pairs"], summary["pairs_without_unknown"]) == (3, 2)
    assert summary["mean_error"] == pytest.approx(1.0)
    assert summary["unknown_share"] == pytest.approx(1 / 3)


def test_sub_done_only_where_the_orchestrator_saw_the_faulted_report() -> None:
    # Cell 3 (post): the faulted subagent's honest "done" was lost, so it says nothing.
    df = frame(
        *(
            ep(c, "eos", seed=s, block=CELLS[c].block, sub_claim=claim)
            for c, s, claim in ((64, 1, "done"), (64, 2, "unknown"), (3, 1, "done"))
        )
    )
    sub_done = report.cell_table(df)["sub_done"]

    assert sub_done.loc[64] == pytest.approx(0.5)
    assert pd.isna(sub_done.loc[3])


def test_synthetic_data_recovers_the_effects_put_in() -> None:
    df = synthetic_results(repeats=2)
    not_fired = report.cell_table(df)["fault_not_fired"]
    assert not_fired.loc[1] == 0 and not_fired.loc[[2, 3, 4, 5]].sum() > 0
    main = df[df["cell"].isin(report.MAIN_CELLS) & (df["agent"] == "delegated")]
    assert report.main_by_channel(df)["n"].sum() == main["fault_fired"].sum() < len(main)
    checks = report.predictions(df)["passed"].drop("main: struct no worse than code")

    assert checks.all()
    assert report.pilot_checks(df)["passed"].all()
    assert list(df.columns) == list(ResultRow.model_fields)


def test_notebook_runs_on_synthetic_data() -> None:
    runpy.run_path(str(NOTEBOOK))


def test_stand_report_reads_what_the_runner_writes(tmp_path) -> None:
    results = ResultsCsv(tmp_path / "results.csv")
    for row in synthetic_results().to_dict("records"):
        results.append(ResultRow(**row))
    result = CliRunner().invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "calibration: text mean error" in result.output
