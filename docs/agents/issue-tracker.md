# Issue tracker: BACKLOG.md

Work is tracked in markdown files at the repo root. Do not create GitHub issues.

- `BACKLOG.md`: what the plan needs but is not done yet. One `- [ ]` line per item,
  prefixed with the task number. Remove the item in the same PR that finishes it.
- `DECISIONS.md`: deviations from the plan. Entry format: `## YYYY-MM-DD · <decision>`,
  then what was decided and why. Open questions go to the numbered list at the bottom
  and are closed on team calls.

New entries match the language of the file they go into.

## When a skill says "publish to the issue tracker"

Append a `- [ ]` item to the matching section of `BACKLOG.md`, with the plan task number
if there is one. A question that needs a team decision goes to the open questions
in `DECISIONS.md` instead.

## When a skill says "fetch the relevant ticket"

Read the relevant `BACKLOG.md` item and every
`DECISIONS.md` entry that mentions it.
