# handoff-bench

Стенд: проходит ли сбой через границу «субагент → оркестратор» при делегировании платежа.

- [DECISIONS.md](DECISIONS.md) — что решили и почему, открытые вопросы
- [BACKLOG.md](BACKLOG.md) — что по плану ещё не сделано

## Быстрый старт

```bash
uv sync
uv run pytest                 # без ключей и без платных вызовов модели
uv run stand skeleton         # один агент платит счёт; нужен OPENROUTER_API_KEY в .env
uv run stand bank serve       # банк по HTTP: http://127.0.0.1:8765/mcp
```

## Где что

| Путь | Что |
|---|---|
| `src/handoff_bench/billing/` | банк: ядро (`service.py`), MCP-сервер (`server.py`), контракт (`protocol.py`), журнал эффектов |
| `src/handoff_bench/agents/` | шлюз вызовов и журнал вызовов, бэкенды инструментов, клиент модели, агентный цикл |
| `src/handoff_bench/runner/` | сборка эпизода (пока скелет) |
| `faults/`, `channels/`, `worlds/`, `grading/` | под задачи 8, 9, 5, 10 |
