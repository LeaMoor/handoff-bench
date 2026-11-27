# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root, if it exists.
- **`DECISIONS.md`** at the repo root: it plays the role of ADRs (there is no `docs/adr/`) and overrides the plan wherever they disagree.

If `GLOSSARY.md` doesn't exist, **proceed silently**. Don't flag its absence; don't suggest creating it upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates it lazily when terms actually get resolved.

## File structure

Single-context repo:

```
/
├── GLOSSARY.md
├── DECISIONS.md        ← decisions (instead of docs/adr/)
├── BACKLOG.md
└── src/handoff_bench/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag conflicts with DECISIONS.md

If your output contradicts an entry in `DECISIONS.md`, surface it explicitly rather than silently overriding:

> _Contradicts DECISIONS 2026-11-12 (no `get_invoice` for the orchestrator in calibration), but worth reopening because…_
