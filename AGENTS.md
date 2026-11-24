# AGENTS.md

Rules for any coding agent (Claude Code, Codex, …) and for people working on handoff-bench:
how to add an experiment without breaking the old ones, how to run it, and how to write up the
result. Coding principles are in [CLAUDE.md](CLAUDE.md); they apply here too.

## Read first

- [DECISIONS.md](DECISIONS.md): what was decided and why. It overrides the plan. If your change
  contradicts an entry, say so in the PR, don't override it silently.
- [docs/tasks-2026-11-20.md](docs/tasks-2026-11-20.md): hypotheses (M1–M8), cells, axes.
- [docs/agents/domain.md](docs/agents/domain.md) and [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md).
- The latest `docs/results/<date>/README.md` for the experiment you extend.

## Language

- Code, comments, docstrings, commit messages, PR titles and descriptions: English.
- `DECISIONS.md`, `docs/results/`, review comments and team docs (`README.md`, `docs/README.md`,
  `docs/raw-logs.md`): Russian, as they are now.

## Adding an experiment

**Old cells must not change.** Every number in `docs/results/` rests on cells 1–73 behaving as
they did when they ran. A new experiment adds cells; it does not change the prompts, tools, tool
schemas, bank behaviour, faults, worlds or grading of existing ones.

- **New cells get explicit numbers**, not `len(_cells) + 1`. Before picking them, check `main` and
  every open PR (`gh pr list`), then add yours to the table below in the same PR.
- **New behaviour goes behind the new cell**: a new `Fault` in `faults/setup.py`, a new `Channel`
  in `channels/render.py`, a new block, or a new class (as `ParallelDelegation` subclasses
  `Delegation`). Shared code (`Delegation`, `run_episode`, `grade`) gets at most a switch that is
  off for old cells.
- **Worlds of templates 1–3 are frozen.** `tests/worlds/test_generator.py::test_grid_fingerprint`
  must keep its hash. A new kind of task is a new template; say which cells may use it and
  validate the pair (`validate_cell_template` in #20).
- **Prove the old cells are untouched.** Run `main`'s test suite against your `src/`: every
  failure must come from something you added on purpose (for example a new template in
  `TEMPLATES`), and the PR says which. For changes to shared code, also run the same scripted
  episodes of cells 1–73 on `main` and on your branch and compare `model_calls.jsonl`,
  `calls.jsonl`, `effects.jsonl` and `result.json`; regrade existing `runs/` with your grader.
- **One implementation per concept.** If another PR already grades a multi-invoice task or builds
  a three-invoice world, reuse or merge with it, don't add a second one.

### Cell numbers

| Cells | What | Where |
|---|---|---|
| 1–73 | control, calibration, main block, B0, M4 keys, statement with key, M3 processing, hedged statement | `main` |
| 74–76 | M8 overlapping worklists and D12 ownership | #15 |
| 77–81 | M3 partial execution, visible and hidden charges | #20 |
| 82–85 | M2 retell channel | #21 |
| 86–93 | Claude Code's own subagents (variant B) | `main-for-harness`, not merged into `main` |

Take the next free number after the highest one here. Update the table when a PR merges or closes.

## Running

- **Config**: `configs/prefreeze/<model>-<experiment>.yaml`, with `run_id: prefreeze-<model>-<experiment>`.
  Pin the provider (`model.provider`). DeepSeek runs on Fireworks: OpenRouter filters the
  `deepseek` provider out by the account's privacy settings (`docs/results/2026-11-21/m3-processing.md`).
- **Clean commit.** `stand batch` refuses a `-dirty` tree (tracked files only). Don't set
  `allow_dirty` outside `configs/debug.yaml`. The commit goes into every `results.csv` row; the
  README names it.
- **Size.** Smoke (a few episodes) → pilot → full: 21 episodes per cell (templates 1–3 × seeds
  1–7), the size of every other block. A different design (one template, other seeds) is stated
  in the README and DECISIONS.
- **Budget**: `budget_usd` per config; the batch stops after it, overshooting by up to `parallel`
  episodes.
- **Invalid episodes** (`api_error`, `infra_error`) are not results: rerun them, don't count
  them. A rerun of the same episode keeps one valid copy.
- **API keys** live only in `.env`, never in configs, logs pasted into PRs, or commits.

## Results

Every finished run gets a folder `docs/results/<YYYY-MM-DD>/` (the day the results were written),
in the same PR as the code or right after it.

- **Tables are built by a script, never typed by hand.** The script lives in `scripts/`, reads
  only `runs/<run_id>/` (`results.csv` and episode files), and its output is committed next to the
  README as `<name>-tables.txt`. Every number in the README must be findable in a tables file. For
  cells shaped like the main block (condition × channel), extend `scripts/grid.py` instead of
  writing a new script.
- **Keyword and regex checks are filters, not verdicts.** When a number depends on what a text
  says (a report keeps a caveat, a message claims a payment), flag with the rule, then read the
  flagged cases and a random sample of the rest, and report the reading.
- **Denominators always**: "7 of 21", not "33%". Say which episodes are excluded (fault did not
  fire, invalid) and how many.
- **Compare like with like**: same templates, seeds, prompts, delay and rules. A baseline from
  another run names that run and its commit.

### README of a results folder

Follow the existing ones (`docs/results/2026-11-20/README.md`, `2026-11-23/README.md`):

1. Title with the date; one paragraph: models, cells, episodes, invalid, cost, `prefreeze`.
2. **Что здесь**: each tables file, what it shows, the script that builds it.
3. **Прогоны**: `run_id`, model, cells, episodes, commit.
4. **Ячейки** (if new): what each cell changes, in one or two sentences.
5. **Главное**: the findings, each with its numbers and denominators.
6. What was not tested and what could change the conclusion.

### DECISIONS entry

Add an entry at the top: `## <date> · <title>`, then **Решили** (what and how it is built),
**Почему**, and, once there are results, **Что вышло** with a link to the results README. Record
the design choices a reader could not guess: what the stand fixes and what the model decides,
prompt differences from other blocks, grading rules, renumbered cells.

### Raw logs

`runs/` is not in git. Upload every run cited in a README to the team Drive folder before the PR
merges: [docs/raw-logs.md](docs/raw-logs.md).

## Pull requests

- Rebase on `main` before review; list the files that will conflict with other open PRs and
  propose a merge order.
- The description says what the experiment measures, the result in two or three sentences, the
  checks run (`uv run pytest`, `ruff check`, `ruff format --check`, the old-cells check), and the
  merge risk.
- Merge only after review.
