# Prefreeze runs, November 19–20, 2026

All runs are marked `prefreeze`: they are for finding failures and estimating effects before preregistration, not primary results. About 2,400 episodes; about $4 on OpenRouter.

## Contents

| File | Description |
|---|---|
| [night-report.md](night-report.md) | Night report: smoke, pilot, steps 3–5 on Haiku 5.5 and GPT-6 Luna; why the initial setup hit a ceiling |
| [manual-labels.md](manual-labels.md) | Manual review of disputed episodes: grader rule for `failed` + `uncertain`, and “delegated a check” |
| [notes.md](notes.md) | Notes from the work, including the harder world, Qwen, and calibration without the loophole |
| [compare-models.txt](compare-models.txt) | Six models: main block (6–11), calibration (2–5), harder grid (6–31) |
| [compare-calibration.txt](compare-calibration.txt) | Calibration with the loophole (2–5) and without it (32–35), six models |
| [night-tables.txt](night-tables.txt) | Tables for night-run steps 3–5 |

## Runs: model, size, and code

Raw logs are in each runner’s `runs/<run>/` directory and shared through Drive (they are not in Git). Configs are in [configs/prefreeze/](../../../configs/prefreeze/).

| Runs | Model | Episodes | Code |
|---|---|---:|---|
| `01-smoke-haiku`, `02-pilot`, `02-pilot-repeats`, `03-calibration`, `04-main-haiku` | Haiku 5.5 | 283 | `main` 9e4fade |
| `01b-smoke-luna`, `05-main-luna` | GPT-6 Luna | 137 | `main` 9e4fade |
| `06-mini-pilot`, `07-calibration-hidden` | Haiku 5.5 | 188 | branch `feat/harder-world` |
| `q1-smoke`, `q2-cal-main`, `q3-grid`, `qwen-calibration-hidden` | Qwen 3.7 Flash | 430 | branch `feat/harder-world` |
| `glm-*` | GLM-5.3-Flash | 430 | branch `feat/harder-world` |
| `ds-*` | DeepSeek V4.1 Flash 1017 | 430 | branch `feat/harder-world` |
| `4omini-*` | GPT-4o-mini 2024-07-18 | 430 | branch `feat/harder-world` |
| `luna-calibration-hidden` | GPT-6 Luna | 84 | branch `feat/harder-world` |

In episode `config.json`, the commit is recorded as `9e4fade…-dirty`: runs used uncommitted changes. For branch runs, this is the commit that added the run folder; cells 32–35 were added later than runs 06 and Qwen and do not affect cells 1–31. In results for night runs 01–05, the coder did not yet distinguish “delegated a check”: recalculate with the current grader and coder using `uv run python scripts/regrade.py runs/prefreeze-0*`.

## Scripts

All scripts are in [scripts/](../../../scripts/). Run them from the repository root with `uv run python`.

| Script | What it does |
|---|---|
| `show.py runs/<run>/<episode>…` | Briefly displays an episode: calls in order, their returns, and the final `finish` |
| `regrade.py runs/<run>…` | Recalculate outcomes and actions with the current grader and coder |
| `calib_trace.py runs/<run>…` | Calibration: what the second delegation was and what the check observed |
| `compare.py` | Compare models (run list is at the top of the script) |
| `compare_calibration.py` | Compare calibration with and without the loophole across all models |
