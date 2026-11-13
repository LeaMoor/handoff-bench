from datetime import datetime
from pathlib import Path
from typing import Annotated, cast, get_args

import typer

from handoff_bench.faults.injector import FaultMode

app = typer.Typer(help="Стенд handoff-bench.", no_args_is_help=True)
bank_app = typer.Typer(help="Банк (MCP-сервер billing).", no_args_is_help=True)
app.add_typer(bank_app, name="bank")


@app.callback()
def main() -> None:
    """Стенд handoff-bench."""
    from dotenv import load_dotenv

    # Before the subcommand parses its options: model and provider can come from .env.
    load_dotenv()


CHANNELS = ("text",)  # struct, code — задачи 9 и 16


@app.command()
def run(
    world: Annotated[int, typer.Option(help="Номер мира")] = 1,
    template: Annotated[int, typer.Option(help="Шаблон задачи: 1 | 2 | 3")] = 1,
    fault: Annotated[str, typer.Option(help="none | pre | post")] = "none",
    channel: Annotated[str, typer.Option(help="text (struct, code — позже)")] = "text",
    seed: Annotated[int, typer.Option(help="Сид варианта мира")] = 0,
    model: Annotated[
        str, typer.Option(envvar="OPENROUTER_MODEL", help="ID модели в OpenRouter")
    ] = "qwen/qwen3.7-flash",
    provider: Annotated[
        str, typer.Option(envvar="OPENROUTER_PROVIDER", help="Провайдер в OpenRouter (закреплён)")
    ] = "alibaba",
    model_seed: Annotated[
        int | None, typer.Option(help="Сид модели; не задан — не передаём")
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Куда писать журналы")] = Path("runs"),
) -> None:
    """Один эпизод «оркестратор → субагент»: журналы в runs/single/<episode_id>/.

    Заготовка до runner (задача 12); грейдера ещё нет — печатает итог эпизода.
    """
    import anyio

    from handoff_bench.agents.model_client import ModelSettings, OpenRouterClient
    from handoff_bench.runner.episode import run_episode
    from handoff_bench.worlds import generate

    faults = get_args(FaultMode)
    if fault not in faults:
        raise typer.BadParameter(f"fault must be one of {', '.join(faults)}")
    if channel not in CHANNELS:
        raise typer.BadParameter(f"channel must be one of {', '.join(CHANNELS)}")
    try:
        w = generate(world, seed, template)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e

    client = OpenRouterClient(ModelSettings(model=model, provider=provider, seed=model_seed))
    episode_id = (
        f"{datetime.now():%Y%m%d-%H%M%S}-w{world}t{template}s{seed}-{fault}-{channel}"
        f"-{model.replace('/', '_')}"
    )
    result = anyio.run(
        lambda: run_episode(
            client,
            world=w,
            episode_dir=runs_dir / "single" / episode_id,
            episode_id=episode_id,
            fault=cast(FaultMode, fault),
        )
    )
    typer.echo(result.model_dump_json(indent=2))


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


@app.command()
def skeleton(
    model: Annotated[
        str, typer.Option(envvar="OPENROUTER_MODEL", help="ID модели в OpenRouter")
    ] = "qwen/qwen3.7-flash",
    provider: Annotated[
        str, typer.Option(envvar="OPENROUTER_PROVIDER", help="Провайдер в OpenRouter (закреплён)")
    ] = "alibaba",
    seed: Annotated[int, typer.Option(help="Сид мира (сейчас — номера списаний в банке)")] = 0,
    model_seed: Annotated[
        int | None, typer.Option(help="Сид модели; не задан — не передаём")
    ] = None,
    temperature: Annotated[float | None, typer.Option(help="Не задано — не передаём")] = None,
    reasoning_effort: Annotated[str | None, typer.Option(help="low | medium | high")] = None,
    runs_dir: Annotated[Path, typer.Option(help="Куда писать журналы")] = Path("runs"),
) -> None:
    """Скелет: один агент платит счёт INV-12. Нужен OPENROUTER_API_KEY в .env."""
    import anyio

    from handoff_bench.agents.model_client import ModelSettings, OpenRouterClient
    from handoff_bench.runner.skeleton import run_skeleton

    settings = ModelSettings(
        model=model,
        provider=provider,
        temperature=temperature,
        seed=model_seed,
        reasoning_effort=reasoning_effort,
    )
    client = OpenRouterClient(settings)
    episode_id = f"{datetime.now():%Y%m%d-%H%M%S}-{model.replace('/', '_')}-s{seed}"
    episode_dir = runs_dir / "skeleton" / episode_id

    result = anyio.run(
        lambda: run_skeleton(client, episode_dir=episode_dir, episode_id=episode_id, seed=seed)
    )
    typer.echo(result.model_dump_json(indent=2))
    ok = result.focal_charges == 1 and (result.finish_args or {}).get("status") == "done"
    typer.echo("ГОТОВО: одно списание, агент сообщил done" if ok else "НЕ ТО, см. журналы")


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
    from handoff_bench.billing.protocol import MCP_PATH
    from handoff_bench.billing.server import serve

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
