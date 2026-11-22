# M8 paired six-model pilot

The harness gave two workers fixed worklists drawn from the same three target invoices. Cell 74
used disjoint lists. Cell 75 assigned the shared invoice to both workers. Cell 76 kept that overlap
and gave worker A exclusive write permission for the shared invoice. All cells used the text
channel, K0, normal bank visibility, and no injected failures.

We ran world 1, template 2, and seeds 1–4 for each of the six models. The world seed determines the
invoice IDs and amounts. Model sampling seeds were unset. The pinned provider and model, the 1,024
completion-token limit, and system prompts stayed the same across all three cells for a given
model. The overlapping worker assignments were identical in cells 75 and 76.
The parent prompt explicitly required one successful payment per invoice to report `done`. The
parent report rates are specific to this prompt and text channel.

The benchmark code was `100945c7ba65a22d5f6448ed33523b475bdb37c2`. The batch runner recorded the
full model settings in each episode's `config.json`. The six configs request 12 episodes each,
with a launch threshold of $0.25 per batch. In-flight episodes can finish after that threshold.

[Haiku](../../../configs/prefreeze/haiku-ownership.yaml) · [GPT-4o-mini](../../../configs/prefreeze/4omini-ownership.yaml) · [Luna](../../../configs/prefreeze/luna-ownership.yaml) · [Qwen](../../../configs/prefreeze/qwen-ownership.yaml) · [GLM](../../../configs/prefreeze/glm-ownership.yaml) · [DeepSeek](../../../configs/prefreeze/ds-ownership.yaml)

OpenRouter rejected the original pinned DeepSeek route with HTTP 404 because the account policy
excluded the provider. Those 12 rejected episodes cost $0 and are outside the paired results.
We reran all three conditions for the same DeepSeek V4.1 Flash 1017 model with
`deepinfra/fp8` pinned and the original account policy in effect. The committed DeepSeek config
now points to that route.

## Results

All 72 paired episodes completed with valid bank traces, four per condition for each model.
Both workers ran concurrently in every episode. OpenRouter reported **$0.07945848** in
total spend.

| Model and provider | Both attempted the shared invoice (75 / 76) | Shared duplicates (75 / 76) | Denied D12 writes | Parent reports in 76 (`done` / `unknown` / no `finish`) | Spend (USD) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Haiku 5.5 (Anthropic) | 4/4 | 4/0 | 4 | 0/4/0 | 0.017406 |
| GPT-4o-mini (OpenAI) | 4/4 | 4/0 | 4 | 4/0/0 | 0.011707 |
| Luna (OpenAI) | 4/4 | 4/0 | 4 | 0/4/0 | 0.010054 |
| Qwen (Alibaba) | 4/3 | 4/0 | 3 | 1/2/1 | 0.007795 |
| GLM (Z.AI) | 4/4 | 4/0 | 4 | 2/2/0 | 0.020260 |
| DeepSeek (DeepInfra FP8) | 3/3 | 3/0 | 3 | 4/0/0 | 0.012238 |
| **Total** | **23/22** | **23/0** | **22** | **11/12/1** | **0.079458** |

The control produced zero duplicates. In the overlap condition, every episode with an attempted
write by both workers produced a duplicate. D12 rejected 22 writes and left exactly
one full charge for the shared invoice in every episode. Across all three cells, both exclusive
invoices were paid once. There were zero omitted payments, wrong amounts, or off-target charges.

With D12, 12 parents returned `unknown`, 11 explicitly included
the shared invoice in `uncertain`, and 1 ended without a structured report. The
ledger showed complete payment in all 24 D12 episodes. The high uncertainty rate in the
[original Haiku pilot](../2026-11-21/m8-overlapping-ownership.md) repeated for Haiku and Luna.
GPT-4o-mini and DeepSeek each returned `done` in all four D12 episodes.

Offline regrading preserved all outcome labels. It corrected one text-audit flag where a
parent accurately reported that the invoice had been charged twice and therefore had not
been paid exactly once.

## Raw traces

The original archive is locally available at `runs/prefreeze-haiku-ownership.tar.gz`. The
extension archive is `runs/m8-six-model-pilot-2026-11-23.tar.gz` and includes all seven
attempted batches with their original episode IDs. Both paths are ignored by Git.
