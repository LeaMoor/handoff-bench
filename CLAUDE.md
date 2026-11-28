## Language

- **Chat with user**: reply in user's language (Russian if they write Russian)
- **Code, comments, docstrings, docs, PRs**: English only

## Principles

- **DRY** — extract repeated logic. But avoid premature abstractions.
- **KISS** — simplest working solution. No CSS-in-JS, no state managers when `useState` works.
- **YAGNI** — don't add features "for the future". Write what's needed now.
- **Localize changes** — don't touch unrelated modules
- **Async-first** for I/O in Python (`async def`)
- **Type everything** — type hints on public functions, concise docstrings
- **Error handling** — catch expected exceptions, avoid blanket `except Exception`
- **No secrets in code** — use `.env`, ship `.env.example`

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Experiments and results

How to add cells, run batches, build tables and write up results: [AGENTS.md](AGENTS.md).

### Upload every run, without being asked

`runs/` is not in git. Raw logs live in the team Google Drive folder `handoff-bench-runs`
(rclone remote `gdrive:`; one-time setup in [docs/raw-logs.md](docs/raw-logs.md)). A batch is not
done until it is on Drive:

1. After a batch whose numbers go into a report, upload it as one archive per run:

   ```bash
   tar -czf "$TMP/<run_id>.tar.gz" -C runs <run_id>
   rclone copy "$TMP/<run_id>.tar.gz" gdrive:
   rclone check "$TMP/<run_id>.tar.gz" gdrive: --one-way --include "<run_id>.tar.gz"  # 0 differences
   ```

2. Broken runs (all episodes invalid, wrong provider, key limit) go to `gdrive:_broken/`.
   A run stopped early (budget, stalled provider) is still a valid run: upload it as usual and
   say in the report how many episodes per cell it has.
3. List every uploaded `run_id` in the report's runs table, and upload before the PR is reviewed.
4. Never commit `runs/` or `~/.config/rclone/rclone.conf` (it holds a Drive token); never paste
   the token anywhere.

### What a run contains

`runs/<run_id>/`:

- `results.csv`: one row per finished episode. Cell factors (`cell`, `block`, `fault`, `channel`,
  `delay`, `rules`, `agent`, `key`), the world (`template`, `seed`, `repeat`), the configured and
  served model and provider, the grade (`outcome`, `correct`, `effect`, `claim`, `sub_claim`,
  `false_done`, `focal_charges`, …), behaviour (`first_action`, `self_checked`, `delegations`,
  `stop_reason`, `steps`), tokens, `cost` and `commit`.
- `<episode_id>/`, named `c<cell>-w<world>-t<template>-s<seed>-r<repeat>`:
  - `config.json`: the episode config (cell factors; prompts, tools, rules and step limits per
    role; `model`, `subagent_model`; `commit`) and the bank's fault settings.
  - `calls.jsonl`: every tool call by every actor (`orchestrator`, `sub-N`): `seq`, `tool`,
    `args`, `outcome`, `result` or error. The statement channel is built from it.
  - `effects.jsonl`: the charges that really happened in the bank. The grader's source of truth.
  - `model_calls.jsonl`: every model request: actor, step, new messages, tools offered, the raw
    API response (served model and provider), usage and cost. `agents.loop.rebuild_requests`
    rebuilds the full prompt of each step.
  - `trace.json`: `orchestrator`, `subagents`, `retells`: messages, finish tool and arguments,
    `stop_reason`.
  - `result.json`: the episode's `results.csv` row. Missing if the episode did not finish.
  - `bank.sqlite`: the bank's final state. `crash.txt`: only if the episode crashed.

To read one episode: `uv run python scripts/show.py runs/<run_id>/<episode_id>`.

## Agent skills

### Issue tracker

Open tasks live in `BACKLOG.md`, not in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `GLOSSARY.md` (once created) and `DECISIONS.md` in place of `docs/adr/`. See `docs/agents/domain.md`.
