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

## Agent skills

### Issue tracker

Tasks live in `BACKLOG.md` (numbered per `docs/plan-weeks-1-2.md`), not in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `GLOSSARY.md` (once created) and `DECISIONS.md` in place of `docs/adr/`. See `docs/agents/domain.md`.
