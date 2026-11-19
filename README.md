# handoff-bench

Стенд: проходит ли сбой через границу «субагент → оркестратор» при делегировании платежа.

- [docs/plan-weeks-1-2.md](docs/plan-weeks-1-2.md) — план на недели 1–2; как с ним работаем — [docs/README.md](docs/README.md)
- [DECISIONS.md](DECISIONS.md) — что решили и почему, открытые вопросы
- [BACKLOG.md](BACKLOG.md) — что по плану ещё не сделано
- [docs/plan-part-1.md](docs/plan-part-1.md) — следующий план: эксперименты части 1 на одном компьютере
- [docs/results/](docs/results/) — прогоны и их разбор; скрипты разбора — `scripts/`
- [docs/tasks-2026-11-20.md](docs/tasks-2026-11-20.md) — задания на ближайшие прогоны и справочник: гипотезы, ячейки, оси, защиты

## Быстрый старт

```bash
uv sync
uv run pytest                 # без ключей и без платных вызовов модели
uv run stand run --template 1 --seed 1 --fault post --channel code   # один эпизод; ключ и модель — в .env (см. .env.example)
uv run stand batch configs/debug.yaml        # пачка → runs/<run_id>/results.csv
uv run stand report runs/<run_id>            # таблицы, главные числа, предсказания
uv run --group notebook jupyter lab          # ноутбук notebooks/week2_analysis.py
uv run stand bank serve --template 1 --seed 1   # банк мира 1 по HTTP: http://127.0.0.1:8765/mcp
```

## Где что

| Путь | Что |
|---|---|
| `src/handoff_bench/billing/` | банк: ядро (`service.py`), MCP-сервер (`server.py`), контракт (`protocol.py`), журнал эффектов |
| `src/handoff_bench/agents/` | шлюз вызовов и журнал вызовов, бэкенды инструментов, клиент модели, агентный цикл, `delegate` и субагент (`delegation.py`) |
| `src/handoff_bench/runner/` | эпизод и его конфиг (`episode.py`), эпизод → оценка → строка (`run.py`), пачка (`batch.py`), схема `results.csv` (`results.py`) |
| `src/handoff_bench/cells.py` | ячейки: контроль, калибровка, главный блок (канал × мир × задержка × правила) и B0 |
| `src/handoff_bench/grading/` | грейдер (`grader.py`) и кодер поведения (`coder.py`) |
| `src/handoff_bench/analysis/` | расчёты по `results.csv` (`report.py`) и отладочные данные (`synthetic.py`) |
| `notebooks/` | ноутбук анализа (jupytext, `.py`) |
| `configs/` | конфиги пачек для `stand batch` |
| `src/handoff_bench/worlds/` | мир 1: генератор вариантов по сиду и шаблону задачи; `stand world` — посмотреть мир |
| `src/handoff_bench/faults/` | инжектор pre / post (`injector.py`) и реестр поломок `FAULTS` (`setup.py`) |
| `src/handoff_bench/channels/` | каналы «слова», «бланк» и «выписка» и реестр `RENDERERS` (`render.py`) |
