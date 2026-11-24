# M8 paired Haiku pilot

The pilot used world 1, template 2, seeds 1–4, and historical cell IDs 70–72. The results tables retain those historical IDs. Raw episode directories and IDs retain their `c70-`, `c71-`, and `c72-` prefixes. The [six-model pilot config](https://github.com/LeaMoor/handoff-bench/blob/dbedd9b5aa8bd24a4d38754df58bad373b723fd7/configs/prefreeze/haiku-ownership.yaml) uses the corresponding cells 74–76 on historical template 2. For a given seed, all three conditions used the same three target invoices, amounts, and distractors. The user asked for exactly one payment per invoice. Both children ran through the same text reporting channel with normal bank visibility, bank and report faults off, and K0. K0 hides the idempotency key field and strips any key sent by a model.

| Historical pilot cell | Current M8 cell | Condition | Worklist A | Worklist B |
| --- | --- | --- | --- | --- |
| 70 | 74 | Disjoint control | 1, 2 | 3 |
| 71 | 75 | Overlap | 1, 2 | 2, 3 |
| 72 | 76 | Overlap with D12 | 1, 2 | 2, 3 |

D12 gives A write permission for invoices 1 and 2 and B write permission for invoice 3. The harness reserves both task and actor IDs before starting either child. The gateway rejects any charge without a manifest entry or from a different task, including charges for distractors. Both children can read bank records. The two overlap conditions use identical prompts and worklists. In the D12 condition the model learns about the restriction when its tool call is denied.

For the archived template-2 design, use the linked config at its historical commit. The current config reruns cells 74–76 and adds cell 94 on template 4. OpenRouter reads its key from `OPENROUTER_API_KEY`. Raw traces for the original cells 70–72 are in the ignored `runs/prefreeze-haiku-ownership/` directory. The per-episode `result.json` and the JSON-valued `ownership` column of `results.csv` record attempts, denials, committed amounts and actors, and the parent report audit. The worklists and manifest are in each `config.json`.

## Live results

Each original episode recorded `58c3b5116b4232886c35072212543bcb5ddb7427-dirty` in its config. The run used uncommitted M8 changes. The first M8 commit was `6ba069604f9df579ee654faad6cf5ebfff344a16`. The exact working tree at the time of the run was not archived.

OpenRouter served Claude Haiku 5.5 through Anthropic. All 12 episodes finished, four per condition, with no crashes, budget skips, or missing cost data. Total spend was **$0.01773132** against the $2 batch budget.

| Historical pilot cell | Valid episodes | Concurrent child runs | Both children attempted invoice 2 | Episodes with a duplicate | D12 denials | Omitted targets | Off-target charges | Spend (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 70 | 4 | 4 | 0 | 0 | 0 | 0 | 0 | 0.00503607 |
| 71 | 4 | 4 | 4 | 4 | 0 | 0 | 0 | 0.00580692 |
| 72 | 4 | 4 | 4 | 0 | 4 | 0 | 0 | 0.00688833 |

Both exclusive invoices were paid once for the correct amount in every episode. Every duplicate involved the shared invoice, which two different actors charged for the full amount in each cell 71 episode. Cell 72 denied every B attempt and committed exactly one full charge from A, including three episodes in which B attempted first. The paired traces confirmed identical initial messages and bank settings for cells 71 and 72.

| Seed | Shared invoice | Intended amount (cents) | Cell 71 commit actors | Cell 72 first attempt | Cell 72 commit actor |
| --- | --- | ---: | --- | --- | --- |
| 1 | INV-255 | 85,400 | B, A | B | A |
| 2 | INV-774 | 87,800 | B, A | B | A |
| 3 | INV-251 | 35,950 | A, B | B | A |
| 4 | INV-444 | 64,350 | B, A | A | A |

## Parent report audit

The current M8 grader also flags direct invoice-status claims and explicit retry instructions that contradict the effect journal. The free-text checks use explicit wording patterns. Manual review covers indirect or disputed statements.

| Historical pilot cell | Parent status in all four episodes | False claims of a correct payment | Correct payments missing from `done` |
| --- | --- | ---: | ---: |
| 70 | `done` | 0 | 0 |
| 71 | `failed` | 0 | 0 |
| 72 | `unknown` | 0 | 4 |

Manual review of the final messages confirmed that all four cell 71 parents identified the double charge. In all four cell 72 episodes, the parent left the shared payment unresolved despite A reporting a successful charge. B reported that its own write had been refused and described the invoice as unpaid or still open. Both workers had read the shared invoice as open before the first write. The parent had only the text reports, so it treated B's denial and A's successful charge as competing accounts of the payment. The effect journal confirms that all three invoices were paid exactly once in each of these episodes.

The pilot covers four paired seeds for one model and one report channel. D12 enforced ownership on the observed attempts. The scripted owner-omission case establishes that a missing owner write remains a skipped payment.

## Scripted verification

The runner tests used an event barrier to prove that both children entered the model loop before either could continue. They checked distinct actor and task IDs across model, call, and bank journals, and assignment-order reports when B finished first. Both children charged the shared invoice in current cell 75. In current cell 76 only A committed, including when B attempted the write first. Both exclusive invoices committed in either order. When A skipped the shared invoice, the grader recorded an omission and flagged the parent's claim of completion. The tests also exercised off-target denial, available reads, single dispatch, and sibling cancellation after an unexpected child failure.
