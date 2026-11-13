# handoff-bench

Стенд: проходит ли сбой через границу «субагент → оркестратор» при делегировании платежа.

- [docs/plan-weeks-1-2.md](docs/plan-weeks-1-2.md) — план на недели 1–2; как с ним работаем — [docs/README.md](docs/README.md)
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
| `src/handoff_bench/agents/` | шлюз вызовов и журнал вызовов, бэкенды инструментов, клиент модели, агентный цикл, `delegate` и субагент (`delegation.py`) |
| `src/handoff_bench/runner/` | эпизод «оркестратор → субагент» (`episode.py`); `skeleton.py` — временный, один агент |
| `src/handoff_bench/worlds/` | мир 1: генератор вариантов по сиду и шаблону задачи; `stand world` — посмотреть мир |
| `src/handoff_bench/grading/` | грейдер: исход эпизода только из журнала эффектов и `finish` оркестратора |
| `faults/`, `channels/` | под задачи 8, 9 |
