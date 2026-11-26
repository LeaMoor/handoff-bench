# M8 owner-aware D12 denial, 25 November 2026

Six models completed 504 valid prefreeze episodes across four cells, for $0.67577447.
A separate four-episode Haiku smoke cost $0.00661170. See the
[generated tables](m8-owner-aware-tables.txt) for run IDs, model routes, costs and outcomes.

```sh
uv run python scripts/m8_tables.py runs/prefreeze-*-ownership-template4
```

## Design

All four cells used world 1, template 4, seeds 1–21 and commit
`5ba2c8c2b31640974a3c1c1a341ea6253dce7fda`. Models ran with the pinned provider in
`configs/prefreeze/*-ownership.yaml`. The harness assigned two fixed worklists and the same
middle invoice to both workers in the overlap conditions. K0 and normal bank visibility
were held fixed. The parent received the workers' text reports and had no bank tools.

| Cell | Condition |
| --- | --- |
| 74 | Disjoint worklists |
| 75 | Overlapping worklists |
| 76 | Same overlap with D12, basic denial |
| 94 | Same overlap with D12, denial names the owner and directs the worker to avoid retrying |

Cells 76 and 94 have identical initial prompts and tool schemas. I compared the bank
configuration, worklists, ownership manifest, model settings and initial model requests
in all 126 pairs. D12 allowed only owner charges in both conditions. Every denial of a
charge with a known owner named that owner and directed the worker to avoid retrying in
cell 94. The notice communicates ownership. Payment evidence comes from the workers' reports.

## Effects and parent reports

| Cell | Episodes with a shared duplicate | All three paid once | Parent `unknown` | Parent produced no structured finish |
| --- | ---: | ---: | ---: | ---: |
| 74 | 0/126 | 126/126 | 10/126 | 0/126 |
| 75 | 119/126 | 5/126 | 26/126 | 29/126 |
| 76 | 0/126 | 126/126 | 46/126 | 18/126 |
| 94 | 0/126 | 125/126 | 29/126 | 5/126 |

The only incomplete bundle under D12 was Qwen at seed 20 in cell 94, where the parent
ended in text without starting either worker. That episode had zero denials. Among the
125 pairs where both bundles were paid exactly once, `unknown` fell from 45 to 29.
There were 28 transitions from `unknown` to `done` and six in the reverse direction.

| Model | Parent `unknown` in 76 | Parent `unknown` in 94 |
| --- | ---: | ---: |
| Haiku 5.5 | 21/21 | 12/21 |
| DeepSeek V4.1 Flash | 1/21 | 0/21 |
| GPT-4o-mini | 2/21 | 8/21 |
| GPT-6 Luna | 15/21 | 2/21 |
| Qwen 3.7 Flash | 4/21 | 4/21 |
| GLM-5.3-Flash | 3/21 | 3/21 |

In cell 94, 123 episodes had a denial on the shared invoice. Worker B passed the literal
owner ID `work-a` in the text returned to the parent in 89 of those episodes. In cell
76, that happened in zero of 124 episodes with a shared-invoice denial. Literal-name
matching is a trace check. A worker could convey the owner without repeating the ID.

The intervention tests the owner name and the retry instruction together. The effect
on parent uncertainty varies by model. The harness fixes assignments, so these results
describe recovery from an assigned overlap. The
[historical six-model pilot](../2026-11-23/m8-six-model-pilot.md) used template 2 and four
seeds per model. It is a separate cohort.

## Artifacts

The tables use the shared invoice recorded in the worklists. The generic focal-invoice
columns refer to the last invoice in template 4. Per-run archives are local pending
upload to the [team Drive folder](https://drive.google.com/drive/folders/1d33p8WVDL3DxKhBYWWvBuVvGcStwSZH2).
