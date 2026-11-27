from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from handoff_bench.runner.batch import BatchConfig, BatchSummary

app = typer.Typer(help="The handoff-bench stand.", no_args_is_help=True)
bank_app = typer.Typer(help="Bank (billing MCP server).", no_args_is_help=True)
app.add_typer(bank_app, name="bank")


def _run_id(run_id: str | None, label: str) -> str:
    return run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{label}"


@app.callback()
def main() -> None:
    """The handoff-bench stand."""
    from dotenv import load_dotenv

    # Before the subcommand parses its options: model and provider can come from .env.
    load_dotenv()


def _run_batch(cfg: "BatchConfig", label: str, runs_dir: Path) -> "BatchSummary":
    import anyio

    from handoff_bench.agents.model_client import OpenRouterClient
    from handoff_bench.runner.batch import DirtyCheckout, run_batch
    from handoff_bench.runner.results import git_commit

    client = OpenRouterClient(cfg.model)
    run_id = _run_id(cfg.run_id, label)
    try:
        return anyio.run(
            lambda: run_batch(
                cfg,
                lambda _: client,
                run_dir=runs_dir / run_id,
                run_id=run_id,
                commit=git_commit(),
            )
        )
    except DirtyCheckout as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e


@app.command()
def run(
    world: Annotated[int, typer.Option(help="World number")] = 1,
    template: Annotated[int, typer.Option(help="Task template: 1 | 2 | 3")] = 1,
    seed: Annotated[int, typer.Option(help="World variant seed")] = 1,
    fault: Annotated[
        str,
        typer.Option(
            help="none | pre | post | timeout_lost | timeout_committed | respawn | redeliver"
            " | processing_settles | processing_fails | capture_completed | capture_declined"
            " (processing, capture: with --no-rules)"
        ),
    ] = "none",
    channel: Annotated[
        str, typer.Option(help="text | struct | code; none for --agent single")
    ] = "text",
    delay: Annotated[
        str, typer.Option(help="short | long (long: the timed-out charge never shows)")
    ] = "short",
    rules: Annotated[bool, typer.Option(help="Rules block for the subagent")] = True,
    agent: Annotated[
        str, typer.Option(help="delegated | single (B0: the orchestrator pays itself)")
    ] = "delegated",
    model: Annotated[
        str, typer.Option(envvar="OPENROUTER_MODEL", help="OpenRouter model ID")
    ] = "qwen/qwen3.7-flash",
    provider: Annotated[
        str, typer.Option(envvar="OPENROUTER_PROVIDER", help="OpenRouter provider (pinned)")
    ] = "alibaba",
    model_seed: Annotated[int | None, typer.Option(help="Model seed; not sent if unset")] = None,
    temperature: Annotated[float | None, typer.Option(help="Not sent if unset")] = None,
    reasoning_effort: Annotated[str | None, typer.Option(help="low | medium | high")] = None,
    run_id: Annotated[str | None, typer.Option(help="Run name (default: timestamp)")] = None,
    runs_dir: Annotated[Path, typer.Option(help="Where to write logs")] = Path("runs"),
) -> None:
    """Run one episode as a batch of one: logs in runs/<run_id>/<episode_id>/, a row in results.csv.

    Requires OPENROUTER_API_KEY in .env.
    """
    import math

    import pydantic

    from handoff_bench.agents.model_client import ModelSettings
    from handoff_bench.cells import UnsupportedCell, find_cell
    from handoff_bench.runner.batch import BatchConfig

    try:
        cfg = BatchConfig(
            run_id=run_id,
            model=ModelSettings(
                model=model,
                provider=provider,
                temperature=temperature,
                seed=model_seed,
                reasoning_effort=reasoning_effort,
            ),
            cells=[find_cell(fault, channel, delay=delay, rules=rules, agent=agent).number],
            world=world,
            templates=[template],
            seeds=[seed],
            budget_usd=math.inf,
            allow_dirty=True,
        )
    except (UnsupportedCell, pydantic.ValidationError) as e:
        raise typer.BadParameter(str(e)) from e
    summary = _run_batch(cfg, "single", runs_dir)
    typer.echo((summary.run_dir / "results.csv").read_text(encoding="utf-8"))
    if summary.crashed:
        typer.echo(f"episode crashed, see crash.txt in {summary.run_dir}")
        raise typer.Exit(code=1)


@app.command()
def batch(
    config: Annotated[Path, typer.Argument(help="Batch YAML config, e.g. configs/debug.yaml")],
    runs_dir: Annotated[Path, typer.Option(help="Where to write logs")] = Path("runs"),
) -> None:
    """Run episodes in parallel, stopping at the budget. Requires OPENROUTER_API_KEY in .env."""
    from handoff_bench.runner.batch import BatchConfig

    summary = _run_batch(BatchConfig.load(config), config.stem, runs_dir)
    typer.echo(summary.model_dump_json(indent=2))


@app.command()
def report(
    paths: Annotated[
        list[Path], typer.Argument(help="Run folders runs/<run_id> or results.csv files")
    ],
) -> None:
    """Per-cell table, headline numbers, predictions and pilot criteria."""
    import pandas as pd

    from handoff_bench.analysis import report as r

    df = r.load_results(*paths)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.precision", 2)
    sections = [
        ("By cell", r.cell_table(df)),
        ("Orchestrator first action", r.first_actions(df)),
        (
            "Headline number 1: main block by channel (where checking does not help)",
            r.main_by_channel(df, **r.PRIMARY),
        ),
        ("Main block by condition", r.main_grid(df)),
        ("Headline number 2: calibration", r.calibration_summary(df)),
        ("Predictions", r.predictions(df)),
        ("Pilot criteria", r.pilot_checks(df)),
    ]
    for title, table in sections:
        typer.echo(f"\n== {title} ==\n{table.to_string()}")


@app.command()
def world(
    template: Annotated[int, typer.Option(help="Task template: 1 | 2 | 3")],
    seed: Annotated[int, typer.Option(help="World variant seed")],
    world: Annotated[int, typer.Option(help="World number")] = 1,
) -> None:
    """Show a world: bank invoices, what to pay, where the fault hits, and the task text."""
    from handoff_bench.worlds import generate

    try:
        w = generate(world, seed, template)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(w.model_dump_json(indent=2))


@app.command()
def version() -> None:
    """Stand version."""
    from handoff_bench import __version__

    typer.echo(__version__)


@bank_app.command("serve")
def bank_serve(
    port: Annotated[int, typer.Option(help="HTTP port")] = 8765,
    host: Annotated[str, typer.Option(help="Host address")] = "127.0.0.1",
    episode_id: Annotated[str | None, typer.Option(help="Episode ID (default: timestamp)")] = None,
    runs_dir: Annotated[Path, typer.Option(help="Where to write logs")] = Path("runs"),
    template: Annotated[int, typer.Option(help="Task template: 1 | 2 | 3")] = 1,
    seed: Annotated[int, typer.Option(help="World variant seed")] = 1,
    visibility_delay: Annotated[int, typer.Option(help="Charge visibility delay")] = 0,
    timeout_invoice: Annotated[
        str | None, typer.Option(help="Invoice on which create_charge times out")
    ] = None,
    timeout_commits: Annotated[
        bool, typer.Option(help="The charge still goes through on timeout")
    ] = False,
) -> None:
    """Serve one episode's bank over HTTP at http://<host>:<port>/mcp, with world 1 invoices."""
    from handoff_bench.billing import BankConfig, BillingService, EffectJournal, TimeoutRule
    from handoff_bench.billing.protocol import MCP_PATH
    from handoff_bench.billing.server import serve
    from handoff_bench.worlds import generate

    episode_id = episode_id or "manual-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    episode_dir = runs_dir / "manual" / episode_id
    timeout = (
        TimeoutRule(invoice_id=timeout_invoice, commits=timeout_commits)
        if timeout_invoice
        else None
    )
    service = BillingService(
        episode_id=episode_id,
        config=BankConfig(visibility_delay=visibility_delay, timeout=timeout),
        journal=EffectJournal(episode_dir / "effects.jsonl"),
        db_path=episode_dir / "bank.sqlite",
        id_seed=seed,
    )
    service.add_invoices(generate(1, seed, template).invoices)

    typer.echo(f"bank: http://{host}:{port}{MCP_PATH}  episode={episode_id}")
    typer.echo(f"effects journal: {episode_dir / 'effects.jsonl'}")
    serve(service, host=host, port=port)
