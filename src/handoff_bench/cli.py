"""Консольная команда `stand`."""

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Стенд handoff-bench.", no_args_is_help=True)
bank_app = typer.Typer(help="Банк (MCP-сервер billing).", no_args_is_help=True)
app.add_typer(bank_app, name="bank")


@app.command()
def run(
    world: Annotated[int, typer.Option(help="Номер мира")] = 1,
    fault: Annotated[str, typer.Option(help="none | pre | post | timeout")] = "none",
    channel: Annotated[str, typer.Option(help="text | struct | code")] = "text",
    seed: Annotated[int, typer.Option(help="Сид варианта мира")] = 0,
) -> None:
    """Один эпизод: журналы и оценка в runs/<run_id>/."""
    typer.echo(f"run: world={world} fault={fault} channel={channel} seed={seed}")
    typer.echo("ещё не реализовано")
    raise typer.Exit(code=1)


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
    seed: Annotated[int, typer.Option(help="Сид для номеров списаний")] = 0,
    visibility_delay: Annotated[int, typer.Option(help="Задержка видимости списания")] = 0,
    timeout_invoice: Annotated[
        str | None, typer.Option(help="Счёт, на котором create_charge ответит таймаутом")
    ] = None,
    timeout_commits: Annotated[
        bool, typer.Option(help="При таймауте списание всё равно проходит")
    ] = False,
) -> None:
    """Поднять банк одного эпизода по HTTP: http://<host>:<port>/mcp

    Пока мира нет, в банке лежат демо-счета (см. billing/demo.py).
    """
    from handoff_bench.billing import BankConfig, BillingService, EffectJournal, TimeoutRule
    from handoff_bench.billing.demo import DEMO_INVOICES
    from handoff_bench.billing.server import MCP_PATH, serve

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
    service.add_invoices(DEMO_INVOICES)

    typer.echo(f"bank: http://{host}:{port}{MCP_PATH}  episode={episode_id}")
    typer.echo(f"журнал эффектов: {episode_dir / 'effects.jsonl'}")
    serve(service, host=host, port=port)
