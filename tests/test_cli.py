from typer.testing import CliRunner

from handoff_bench import __version__
from handoff_bench.cli import app


def test_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_run_rejects_unknown_fault_and_channel_before_any_model_call() -> None:
    for args in (["--fault", "timeout"], ["--channel", "code"], ["--template", "9"]):
        result = CliRunner().invoke(app, ["run", *args])
        assert result.exit_code != 0
