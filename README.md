# handoff-bench (Relay)

Relay studies whether a failure survives the “subagent → orchestrator” boundary when a payment is delegated.

## Start here

- [Main research document](docs/delegation-and-exactly-once.md) — full English translation of the project’s hypotheses, literature review, preregistration, and benchmark design.
- [What the Relay experiments show](docs/results/guide.md) — key findings, open checks, and links to experiment reports.
- [Statistical report](docs/results/2026-11-26/statistical-report.md) — confidence intervals, paired comparisons, and the evidence available so far.
- [Experiment results](docs/results/) — runs and their analyses; analysis scripts are in `scripts/`.
- [Decisions](DECISIONS.md) — what was decided, why, and open questions.
- [Backlog](BACKLOG.md) — remaining work.
- [Contributor guide](AGENTS.md) — rules for adding cells, running batches, building tables, and writing up results.
- [Raw run logs](docs/raw-logs.md) — shared Google Drive instructions for uploading and downloading archives.

## Quick start

```bash
uv sync
uv run pytest                 # no keys or paid model calls
uv run stand run --template 1 --seed 1 --fault post --channel code   # one episode; key and model in .env (see .env.example)
uv run stand batch configs/debug.yaml        # batch → runs/<run_id>/results.csv
uv run stand report runs/<run_id>            # tables, primary metrics, predictions
uv run --group notebook jupyter lab          # notebook: notebooks/week2_analysis.py
uv run stand bank serve --template 1 --seed 1   # world 1 bank over HTTP: http://127.0.0.1:8765/mcp
```

## Repository map

| Path | Contents |
|---|---|
| `src/handoff_bench/billing/` | Bank core (`service.py`), MCP server (`server.py`), contract (`protocol.py`), effect journal |
| `src/handoff_bench/agents/` | Tool gateway and call journal, tool backends, model client, agent loop, `delegate`, and subagent (`delegation.py`) |
| `src/handoff_bench/runner/` | Episode and config (`episode.py`), episode → grading → row (`run.py`), batch (`batch.py`), `results.csv` schema (`results.py`) |
| `src/handoff_bench/cells.py` | Experiment cells: control, calibration, main block (channel × world × delay × rules), and B0 |
| `src/handoff_bench/grading/` | Grader (`grader.py`) and behavior coder (`coder.py`) |
| `src/handoff_bench/analysis/` | `results.csv` analysis (`report.py`) and synthetic data (`synthetic.py`) |
| `notebooks/` | Analysis notebook (Jupytext, `.py`) |
| `configs/` | `stand batch` configurations |
| `src/handoff_bench/worlds/` | World 1 and seed-based task-template generator; inspect a world with `stand world` |
| `src/handoff_bench/faults/` | pre / post injector (`injector.py`) and fault registry `FAULTS` (`setup.py`) |
| `src/handoff_bench/channels/` | “words,” “form,” and “statement” channels and `RENDERERS` registry (`render.py`) |
