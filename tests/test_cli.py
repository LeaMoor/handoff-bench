from typer.testing import CliRunner

from handoff_bench import __version__
from handoff_bench.cli import app


def test_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_run_rejects_unknown_fault_and_channel_before_any_model_call() -> None:
    for args in (["--fault", "timeout"], ["--channel", "struct"], ["--template", "9"]):
        result = CliRunner().invoke(app, ["run", *args])
        assert result.exit_code != 0


def test_run_in_tests_cannot_reach_openrouter() -> None:
    # conftest blanks the key, so even valid arguments stop before any paid call.
    result = CliRunner().invoke(app, ["run", "--template", "1", "--seed", "1"])
    assert result.exit_code != 0
    assert "OPENROUTER_API_KEY is not set" in str(result.exception)
