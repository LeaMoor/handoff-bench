from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from handoff_bench.runner.batch import BatchConfig, BatchSummary

app = typer.Typer(help="Стенд handoff-bench.", no_args_is_help=True)
bank_app = typer.Typer(help="Банк (MCP-сервер billing).", no_args_is_help=True)
app.add_typer(bank_app, name="bank")


def _run_id(run_id: str | None, label: str) -> str:
    return run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{label}"


@app.callback()
def main() -> None:
    """Стенд handoff-bench."""
    from dotenv import load_dotenv

    # Before the subcommand parses its options: model and provider can come from .env.
    load_dotenv()


def _run_batch(
    cfg: "BatchConfig", label: str, runs_dir: Path, *, resume: bool = False
) -> "BatchSummary":
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
                resume=resume,
            )
        )
    except DirtyCheckout as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e


@app.command()
def run(
    world: Annotated[int, typer.Option(help="Номер мира")] = 1,
    template: Annotated[int, typer.Option(help="Шаблон задачи: 1 | 2 | 3")] = 1,
    seed: Annotated[int, typer.Option(help="Сид варианта мира")] = 1,
    fault: Annotated[
        str,
        typer.Option(
            help="none | pre | post | timeout_lost | timeout_committed | respawn | redeliver"
            " | processing_settles | processing_fails (processing: with --no-rules)"
        ),
    ] = "none",
    channel: Annotated[
        str, typer.Option(help="text | struct | code; none for --agent single")
    ] = "text",
    delay: Annotated[
        str, typer.Option(help="short | long (long: the timed-out charge never shows)")
    ] = "short",
    rules: Annotated[bool, typer.Option(help="Блок правил у субагента")] = True,
    agent: Annotated[
        str, typer.Option(help="delegated | single (B0: платит сам оркестратор)")
    ] = "delegated",
    model: Annotated[
        str, typer.Option(envvar="OPENROUTER_MODEL", help="ID модели в OpenRouter")
    ] = "qwen/qwen3.7-flash",
    provider: Annotated[
        str, typer.Option(envvar="OPENROUTER_PROVIDER", help="Провайдер в OpenRouter (закреплён)")
    ] = "alibaba",
    model_seed: Annotated[
        int | None, typer.Option(help="Сид модели; не задан — не передаём")
    ] = None,
    temperature: Annotated[float | None, typer.Option(help="Не задано — не передаём")] = None,
    reasoning_effort: Annotated[str | None, typer.Option(help="low | medium | high")] = None,
    run_id: Annotated[str | None, typer.Option(help="Имя прогона (по умолчанию — время)")] = None,
    runs_dir: Annotated[Path, typer.Option(help="Куда писать журналы")] = Path("runs"),
) -> None:
    """Один эпизод — пачка из одного: журналы в runs/<run_id>/<episode_id>/, строка в results.csv.

    Нужен OPENROUTER_API_KEY в .env.
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
        typer.echo(f"эпизод упал, см. crash.txt в {summary.run_dir}")
        raise typer.Exit(code=1)


@app.command()
def batch(
    config: Annotated[Path, typer.Argument(help="YAML-конфиг пачки, например configs/debug.yaml")],
    runs_dir: Annotated[Path, typer.Option(help="Куда писать журналы")] = Path("runs"),
    resume: Annotated[
        bool,
        typer.Option(help="Продолжить прогон с тем же run_id: готовые эпизоды не перезапускать"),
    ] = False,
) -> None:
    """Пачка эпизодов параллельно, с остановкой по бюджету. Нужен OPENROUTER_API_KEY в .env."""
    from handoff_bench.runner.batch import BatchConfig

    cfg = BatchConfig.load(config)
    if resume and cfg.run_id is None:
        raise typer.BadParameter("--resume needs run_id in the config")
    summary = _run_batch(cfg, config.stem, runs_dir, resume=resume)
    typer.echo(summary.model_dump_json(indent=2))
    if summary.limit_reached:
        typer.echo(
            f"\nОстановлено по лимиту подписки ({summary.limit_reached}). После сброса:\n"
            f"  uv run stand batch {config} --resume",
            err=True,
        )


@app.command()
def report(
    paths: Annotated[
        list[Path], typer.Argument(help="Папки прогонов runs/<run_id> или файлы results.csv")
    ],
) -> None:
    """Таблица по ячейкам, главные числа, предсказания и критерии пилота."""
    import pandas as pd

    from handoff_bench.analysis import report as r

    df = r.load_results(*paths)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.precision", 2)
    sections = [
        ("По ячейкам", r.cell_table(df)),
        ("Первое действие оркестратора", r.first_actions(df)),
        (
            "Главное число 1: главный блок по каналам (где проверка не спасает)",
            r.main_by_channel(df, **r.PRIMARY),
        ),
        ("Главный блок по условиям", r.main_grid(df)),
        ("Главное число 2: калибровка", r.calibration_summary(df)),
        ("Предсказания", r.predictions(df)),
        ("Критерии пилота", r.pilot_checks(df)),
    ]
    for title, table in sections:
        typer.echo(f"\n== {title} ==\n{table.to_string()}")


@app.command()
def world(
    template: Annotated[int, typer.Option(help="Шаблон задачи: 1 | 2 | 3")],
    seed: Annotated[int, typer.Option(help="Сид варианта мира")],
    world: Annotated[int, typer.Option(help="Номер мира")] = 1,
) -> None:
    """Показать мир: счета в банке, что оплатить, где ломаем и текст задачи."""
    from handoff_bench.worlds import generate

    try:
        w = generate(world, seed, template)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(w.model_dump_json(indent=2))


@app.command()
def version() -> None:
    """Версия стенда."""
    from handoff_bench import __version__

    typer.echo(__version__)


@bank_app.command("serve")
def bank_serve(
    port: Annotated[int, typer.Option(help="HTTP-порт")] = 8765,
    host: Annotated[str, typer.Option(help="Адрес")] = "127.0.0.1",
    episode_id: Annotated[
        str | None, typer.Option(help="ID эпизода (по умолчанию — время)")
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Куда писать журналы")] = Path("runs"),
    template: Annotated[int, typer.Option(help="Шаблон задачи: 1 | 2 | 3")] = 1,
    seed: Annotated[int, typer.Option(help="Сид варианта мира")] = 1,
    visibility_delay: Annotated[int, typer.Option(help="Задержка видимости списания")] = 0,
    timeout_invoice: Annotated[
        str | None, typer.Option(help="Счёт, на котором create_charge ответит таймаутом")
    ] = None,
    timeout_commits: Annotated[
        bool, typer.Option(help="При таймауте списание всё равно проходит")
    ] = False,
) -> None:
    """Поднять банк одного эпизода по HTTP: http://<host>:<port>/mcp, со счетами мира 1."""
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
    typer.echo(f"журнал эффектов: {episode_dir / 'effects.jsonl'}")
    serve(service, host=host, port=port)
