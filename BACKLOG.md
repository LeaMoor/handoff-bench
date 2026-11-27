# Backlog

Items that remain open from the project work. Decisions are recorded in [DECISIONS.md](DECISIONS.md).

## Infrastructure

- [ ] **CI**: GitHub Actions on every PR: `uv sync`, `uv run ruff check .`, `uv run pytest`. No keys or paid model calls are needed; tests already work this way.

## For tasks in other parts

- [ ] Compare grader and coder with manual labels on real episodes; rules are in `tests/grading/test_tables.py`.
- [ ] Run 10 real episodes with one command: `uv run stand batch configs/debug.yaml`.
- [ ] **`stand pack`**: add a command to archive a batch for the shared drive, as listed among the common plan tasks.
- [ ] Run the notebook on the first real `results.csv` files.
- [ ] Calculate the `api_error` / `infra_error` rate by cell. In post, the subagent runs longer than in pre, so it may fail more often; failures may not be evenly distributed across cells.

## Architecture

- [ ] **Versioned contracts.** Several components depend on file formats: logs (`effects.jsonl`, `calls.jsonl`, `model_calls.jsonl`), episode `config.json`, `results.csv` (`ResultRow`), and the `delegate` response. There are no versions today: changing a column can silently break old `results.csv` files in the notebook. Options: add `schema_version` to each row or validate columns when reading.
- [ ] **Put the orchestrator behind an interface (week 3).** It is currently assembled inside `run_episode` (`AgentSpec` + `run_agent`). Claude Code and Codex need an interface that accepts a task and MCP tools and returns `finish`, without changing the grader, coder, or analysis.

## Experiments

- [ ] **M4 cost, instalments (L1).** A task that pays one invoice in two equal parts: with the intent key (K4) or a pinned request (K5) the second part replays the first, so half stays unpaid while the agent sees success. Needs a new world template and a grader switch for two expected charges; cells 99–102 cover only the declined-capture side.
- [ ] **M5 on real harnesses only.** Context compression while a child is in flight, on Claude Code, Hermes and Codex (native subagents). Not run on our orchestrator, where our own compression prompt would decide what survives.

## Open

- [ ] What counts as a “step”? See open question 1 in DECISIONS.
