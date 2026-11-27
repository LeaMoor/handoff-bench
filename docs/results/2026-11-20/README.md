# Top-up to 21 episodes per cell, November 20, 2026

All runs are marked `prefreeze`, like the [November 19 runs](../2026-11-19/README.md). There were 3,431 episodes, $6.90 on OpenRouter, and no invalid episodes. All six models (Haiku 5.5, GPT-6 Luna, Qwen 3.7 Flash, GLM-5.3-Flash, DeepSeek V4.1 Flash, GPT-4o-mini) now have 21 episodes in cells 1–35, 48–59, and 70–73 (statement with caveat; runs originally used cell numbers 64–67, see DECISIONS).

## Contents

| File | Description |
|---|---|
| [grid-tables.txt](grid-tables.txt) | Grid 6–31 and statement-with-caveat cells 70–73: outcomes by condition (delay, rules, B0) and channel, all models (`scripts/grid.py`) |
| [who-duplicates.txt](who-duplicates.txt) | Who made the second charge: the same subagent or a new one (`scripts/who_duplicates.py`) |
| [keys-hidden-tables.txt](keys-hidden-tables.txt) | M4 when the charge is hidden (48–59), all models, old hiding rule (`scripts/keys.py`) |
| [hidden-rerun-tables.txt](hidden-rerun-tables.txt) | Calibration without the loophole (32–35) and statement with key (60–61): old vs. corrected hiding rule (`scripts/hidden_rerun.py`) |
| [keys-rerun-tables.txt](keys-rerun-tables.txt) | M4 when the charge is hidden (48–59): old vs. corrected hiding rule and Claude Opus 5.5 (`scripts/keys_rerun.py`) |
| [code-key-tables.txt](code-key-tables.txt) | Statement with key (60–63) vs. ordinary statement: outcomes by cell, old vs. corrected hiding rule (`scripts/code_key.py`; its transfer count includes any second delegation—see focal-key) |
| [focal-key-tables.txt](focal-key-tables.txt) | Does the orchestrator pass on the subagent's key when delegating the same invoice again? (`scripts/focal_key.py`) |

## Runs

| Runs | Model | Cells | Episodes |
|---|---|---|---:|
| `haiku-grid-t3`, `haiku-grid-s3to7` | Haiku 5.5 | 12–27, 30, 31 | 306 |
| `ds-grid-*`, `ds-keys-hidden-topup*` | DeepSeek V4.1 Flash | 12–27, 30, 31; 48–59 | 353 |
| `glm-grid-*`, `glm-grid12to23-*`, `glm-keys-hidden*` | GLM-5.3-Flash | 12–27, 30, 31; 48–59 | 558 |
| `4omini-grid-*` | GPT-4o-mini | 12–31 | 340 |
| `qwen-grid-*`, `qwen-keys-hidden` | Qwen 3.7 Flash | 12–31; 48–59 | 592 |
| `luna-cal`, `luna-grid`, `luna-keys-hidden` | GPT-6 Luna | 1–5; 12–31; 48–59 | 777 |
| `*-hedged` | all six | 70–73 (originally 64–67; see DECISIONS) | 504 |
| `*-hidden-v2` (32, 34, 60), `*-hidden-v3` (33, 35, 61) | all six | 32–35; 60–61 for Haiku, DeepSeek, GLM | 630 |
| `*-keys-hidden-v3` | all six | 48–59 | 1,512 |
| `opus-cal-keys` | Claude Opus 5.5 | 32–35; 48, 49, 50, 52, 54, 55, 56, 58 (9 each) | 108 |

New episodes fill in template 3 for all seeds and templates 1–2 for seeds 3–7; the original four were templates 1–2 for seeds 1–2. `*-fill` contains three episodes missed because of budget and one that failed with a network error (`httpx ReadError`). For GPT-4o-mini M4, use `4omini-keys-hidden-rerun`; the run interrupted by a key limit, `4omini-keys-hidden`, is in `runs/_broken-403/`.

**Run code.** Each `results.csv` records its commit. Before November 21, `-dirty` was also set for untracked files (DECISIONS). Commits `947858c` and `cea0986` are not in `main` history; they are tagged `runs/prefreeze-947858c` and `runs/prefreeze-cea0986`.

| Commit | Runs |
|---|---|
| `5e9b6a7` (in `main`) | `*-grid-t3`, `*-grid-s3to7`, `*-fill`, `glm-grid12to23-*`, `luna-cal`, `luna-grid`, `glm-`, `luna-`, `qwen-keys-hidden`, `ds-keys-hidden-topup*` |
| `947858c` (tag) | `*-hedged`, `*-hidden-v2`, `*-hidden-v3`, `opus-cal-keys`—used uncommitted code; final version is `cea0986` |
| `cea0986` (tag) | `*-keys-hidden-v3`, except DeepSeek |
| `32d1d40` (in this PR) | `ds-keys-hidden-v3` |

## Main findings

**M4 holds across all six models.** When verification is unavailable, duplicate rates on retry are 98–100% with no key, 57–100% with a subagent key, 0–26% with an orchestrator-provided key, 36–67% with a task-number key, and 0% for system keys based on payment intent or a pinned first request. Reruns with the corrected hiding rule (`*-keys-hidden-v3`) show the same pattern within noise.

**A statement increases duplicates in the main block.** With a long delay, in the world where the charge succeeded but is not visible, DeepSeek rises from 5% with words to 38% with the statement; GLM from 29% to 71%; Luna with rules from 14% to 76%. Haiku is unchanged (5%). The statement shows “create_charge: timeout, outcome unknown” and “list_charges: no charges” as facts; the orchestrator's own check is also empty, so it delegates another payment. In words, the subagent usually adds “do not retry.” In calibration without the loophole, the statement instead removes duplicates because it shows the successful charge.

**A caveat in the statement reduces boundary duplicates for some models.** Cells 70–73 (`*-hedged`, six × 84 episodes, $1.10) add “the timeout may have succeeded; checks may lag.” The table shows episodes in the world where the first payment succeeded, grouped by who made the second charge (`scripts/who_duplicates.py`, from the effects log):

| Model | New subagent: words / statement / caveat | Same subagent (independent of channel) |
|---|---|---|
| Haiku 5.5 | 0% / 0% / 0% | 0–2% |
| GPT-6 Luna | 12% / 57% / **5%** | 0% |
| DeepSeek V4.1 Flash | 7% / 38% / **12%** | 0–5% |
| GLM-5.3-Flash | 19% / 67% / 62% | 2–7% |
| Qwen 3.7 Flash | 2% / 12% / 7% | 21–26% |
| GPT-4o-mini | 5% / 7% / 5% | 83–90% |

In the world where the charge did not happen, correctness does not fall; honest “I don't know” responses increase. GLM repeats the caveat (“this may be lag”) but asks for a check, and the new subagent sees an empty ledger and pays. The report channel does not prevent duplicates made by the same subagent; the system-issued key (M4) addresses those.

**Calibration without the loophole after the stand fix.** Previously, all charges on an invoice were hidden, including a legitimate retry, creating false duplicates in the “before payment” world (DeepSeek 33%, GLM 62% with the statement). Now only the first charge is hidden (DECISIONS, 2026-11-21). Those false duplicates disappeared. Pair error (mean across both worlds)—words / statement / statement with key: Haiku 21% / 2% / 0%, DeepSeek 43% / 21% / 7%, GLM 40% / 17% / 12%, Luna 48% / 7%, GPT-4o-mini 52% / 33%, Qwen 83% / 76%. The conclusion remains: with words, the “after payment” world has many duplicates; the statement removes them. The orchestrator passes a key from the statement about as often as before (`scripts/focal_key.py`): among episodes where it delegated the same invoice again after the first subagent paid with its key, the key reached the new task in 22/34 DeepSeek episodes, 21/37 GLM, and 7/27 Haiku; never with the ordinary statement. These transfers occurred in calibration “after payment” (61) and in the main block where the charge went through (63), never “before payment” (60).

**Claude Opus 5.5 does not find a workaround on its own** (`opus-cal-keys`, 9 episodes per cell, $7.55, corrected stand). In calibration without the loophole, “after payment”: with words, all 9/9 duplicated (more than any other model: it delegates a check, sees “no charges,” delegates a payment, then reports “paid”); with a statement, there were 0 duplicates and 7/9 “I don't know.” M4 with an invisible charge: 18/18 duplicates with no key; with a subagent key, 22% on respawn and 44% on redelivery, compared with 57–100% for the other models; orchestrator and system keys, 0%.

**B0 is no better than delegation:** one agent duplicated in 29% of GLM, 57% of Qwen, and 71% of GPT-4o-mini episodes. The error comes from late commit, not the delegation boundary.
