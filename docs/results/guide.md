# Relay: What the experiments show

Relay tests what happens to a task at the boundary between two LLM agents. In our simulated bank, an orchestrator delegates a payment to a worker. The benchmark can interrupt the task after an earlier payment or delay when a charge appears in the account history. The bank journal lets us answer three separate questions: how many times money was charged, what the worker understood, and what the orchestrator told the user.

## One episode

The user asks to pay a particular invoice. In the simulator, every invoice has a customer, amount, and status. In the simplest template, the agent must select one invoice among unrelated ones. In the second, it must pay two invoices for one customer in order; the fault affects the second. In the third, similar invoices for the same customer sit side by side for adjacent months. The seed determines the exact task data. A three-invoice batch was later added for partial-execution experiments.

The orchestrator reads the task and delegates it to a worker. The worker calls the bank and returns a report. The key decision comes when the bank times out on a payment attempt. In one world, the money was charged; in the other, the operation was lost. The agent sees the same response in both worlds. Retrying is sensible in the second and dangerous in the first. Stopping also has a cost: the user may be left with an unpaid invoice. The grader compares the final response with a separate journal of actual effects, which agents cannot access directly.

## Why the conditions became harder

**The first results were nearly perfect.** On the night of November 19, we ran 420 episodes with Haiku and Luna. Across the three main series that night—357 episodes—there were no duplicate charges. In the main block, Haiku had 126 correct outcomes out of 126, and Luna had 123 out of 126. A justified “outcome unknown” counts as correct, so we also track whether the user actually received a paid invoice. [First-night report](2026-11-19/night-report.md)

**Traces showed why the task was easy.** While the worker re-read history and wrote its report, the bank’s short delay had time to pass. The parent opened the statement and saw the charge. In calibration, the parent lost direct access to checks and found another route: it delegated a read-only status check to a new agent. Thus, even after losing the report, the team could still learn the truth. Early logs counted both “check” and “pay again” as repeated delegation. We had to inspect what the second worker actually did to distinguish them. [Trace analysis](2026-11-19/notes.md)

**We then changed when a payment becomes visible.** In the hard condition, the first charge whose response was lost remained invisible until the episode ended. We separately varied worker instructions, report format, and whether the task could be done by one agent. Making the world harder did not make every model fail in the same way: in a 104-episode pilot, Haiku still had no duplicates. Under long delay without rules, it finished with “unknown” in 23 of 24 cases. [Notes on the harder world](2026-11-19/notes.md)

**Differences appeared when a check could no longer help.** In a corrected calibration run, the worker paid, its report was lost, and the charge remained hidden. With a text report, duplicates occurred in 43% of Haiku episodes, 76% of GLM episodes, and 86% for both Luna and DeepSeek. When the same scenario showed the parent an already completed action in the journal, risk fell. But after a bank timeout, the journal itself could say only “timeout” and “no charges yet.” An empty statement could then *increase* risk. The benchmark version matters: the original rule also hid later charges; the corrected rule hides only the first problematic charge. [Corrected calibration](2026-11-20/hidden-rerun-tables.txt) · [Benchmark decisions](../../DECISIONS.md)

## Hypotheses behind the experiments

| Hypothesis | Experimental question | What the prefreeze work has tested |
|---|---|---|
| M1 · The boundary itself | Is delegation still worse after giving a single agent extra steps? | Compared a single agent, delegation, and two B1 controls. On matched worlds, there is no clear universal harm from delegation. |
| M2 · Passing uncertainty | When a child reports “I don’t know,” does the parent preserve the meaning of that caveat and act on it? | Words, form, statement, a delay warning, and a retell produced different results depending on report content and model. |
| M3 · Recovery after failure | Can the team determine what work is complete and describe the outcome truthfully? | Tested failures before and after payment, an unfinished batch, “processing” status, and a decline inside a completed order. |
| M4 · Action identity | Does a retry of the same payment keep the same key when the task or worker changes? | Tested key sources, redelivery, and a new run. Retries were also observed with native subagents in three harnesses. |
| M5 · Context compression | Does a parent remember an unfinished delegation if its context is compressed before the child responds? | A separate experiment with a real harness is planned. |
| M6 · User restriction | Does “prepare this, but do not send without my approval” survive in the child’s task and the parent’s later decision? | The scenario has been designed. Payment experiments do not measure violations of this approval rule. |
| M7 · Equal conditions for roles | Could an error be explained by different rules, models, or tools for parent and child? | The grid varied the rule block. A full control needs an audit of actual requests, tool schemas, and safe actions available. |
| M8 · Two workers, one task | What happens when two agents receive the same invoice? | Measured duplicates under assigned overlap and the effect of limiting write access to one owner. How often a planner creates overlap remains a separate question. |

## Three results to start with

1. **Can uncertainty disappear during handoff?** The bank returned “processing,” and the money was ultimately not charged. With Qwen, the worker usually made the false success claim itself. With GPT-4o-mini, all 27 false “paid” messages out of 63 episodes came after the worker had preserved uncertainty. The same final error can have different causes. [M3 report](2026-11-21/m3-processing.md)
2. **What does the next worker see?** In a three-invoice task, the first worker was interrupted midway through the batch. In matched episodes, later workers repeated 0 of 72 payments already made when history was visible, versus 46 of 72 when it was hidden. The unit is an invoice that had already been paid. [Partial-batch report](https://github.com/LeaMoor/handoff-bench/blob/1224790964eecd3b997ed6abc7f506ee1ce0e232/docs/results/2026-11-23/m3-partial-visibility.md)
3. **What survives a retry?** On our own orchestrator, we compared a model-chosen key, a task key, and a payment key. In native-subagent runs with no key, duplicate charges occurred in 6/21 Claude Code episodes, 9/21 Hermes episodes, and 18/21 Codex episodes after a lost response. With a stable key per payment, each had 0/21. This is an observation across three model–harness combinations; isolating the harness effect requires the same models. Codex data in this review are local. [Benchmark grid](2026-11-20/README.md) · [Claude Code and Hermes audit](https://github.com/LeaMoor/handoff-bench/blob/6db9330772c7d950bfe60b027373fb83a529ad91/docs/results/2026-11-24/harness-native-comparison.txt)

## Four more experiments

| Question | Prefreeze result | Details |
|---|---|---|
| Are extra single-agent steps enough to cause duplicates? | Across 36 matched model–world combinations, duplicates were 10 for a single agent, 11 with delegation, 15 for a single agent with extra checks, and 14 with an erroneous check. The comparison combines extra length and the assigned action sequence. | [M1, B1, and controls](https://github.com/LeaMoor/handoff-bench/blob/f6d331ab99a8fba75569d8f13377e51b072f98a4/docs/results/2026-11-25/m1-b1.md) |
| Does a caveat disappear in a retell? | Under a neutral retell of the full history, lexical signs of uncertainty survived in 396/419 reports. This is a keyword search; context compression needs its own experiment. | [M2 retell](https://github.com/LeaMoor/handoff-bench/blob/87970f402893c6c6739043eb72611d43ee1b0ca3/docs/results/2026-11-23/m2-retell.md) |
| Is a completed order confused with a successful payment? | With a declined payment inside a completed order, Qwen passed the false “paid” claim to the user in 27/63 episodes. For other models, the parent could sometimes correct the worker by checking the invoice. | [M3, decline inside order](https://github.com/LeaMoor/handoff-bench/blob/d485219781304ad7d70900ec2c3ae2d62f84b3de/docs/results/2026-11-23/m3-capture.md) |
| Does assigning one write owner help? | With assigned overlap, a shared invoice was charged twice in 119/126 episodes. Both write-restriction variants had 0/126 duplicates. When a denial named the owner and said not to retry, parent “unknown” responses fell from 46 to 29 of 126. | [M8, six-model pilot](https://github.com/LeaMoor/handoff-bench/blob/5a17790124aa1f203c8a79256cd51b69c2ae0941/docs/results/2026-11-25/m8-owner-aware.md) |

## How to read the numbers

These are exploratory runs from before protocol freeze. In native runs, 21 episodes per condition means three templates × seven seeds. Zero observed duplicates does not guarantee future safety or that every payment was completed. To compare conditions, use the same worlds, benchmark version, and clearly stated denominators. In a multi-invoice task, “finished the task” can differ from whether each invoice was actually paid.

The history of visibility rules and metrics is in [DECISIONS](../../DECISIONS.md). To reproduce a result, choose a report, use its code version and `run_id`, download the archive using the [raw-log instructions](../raw-logs.md), and rebuild the table with that version’s script. Reports tied to separate branches refer to their own code snapshots. The availability of a raw archive is separate from whether its table is in Git.

## Statistical analysis

[Behavior of six models on our orchestrator, bank outcomes, user-facing reports, and complete harness runs](2026-11-26/statistical-report.md). The directory also contains scripts and recalculated tables; the source and limitations of each sample are described in the report.
