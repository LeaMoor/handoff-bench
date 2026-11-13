"""Debug `results.csv` with known effects: the notebook must recover what is put in here."""

import random

import pandas as pd

from handoff_bench.cells import CELLS
from handoff_bench.grading.grader import CORRECT
from handoff_bench.runner.results import ResultRow

# Orchestrator's reaction to the empty report in calibration, words channel.
TEXT_REACTION = {"finish_done": 0.6, "redelegate": 0.3, "finish_unknown": 0.1}
MAIN_CORRECT = {"text": 0.4, "struct": 0.55, "code": 0.85}
MAIN_SELF_CHECK = {"text": 0.2, "struct": 0.3, "code": 0.5}
CONTROL_EOS = 0.95
FAULT_NOT_FIRED = 0.05  # the subagent never reached the payment


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _episode(cell: int, reaction: str, rng: random.Random) -> dict[str, object]:
    c = CELLS[cell]
    if c.block == "control":
        ok = rng.random() < CONTROL_EOS
        return {
            "outcome": "eos" if ok else "false_done",
            "claim": "done",
            "first_action": "finish_done",
            "false_done": not ok,
        }
    if c.block == "calibration":
        pre = c.fault == "pre"
        if reaction == "finish_unknown":
            return {
                "outcome": "honest_unknown",
                "claim": "unknown",
                "first_action": reaction,
                "effect": "skip" if pre else "once",
            }
        if reaction == "redelegate":
            outcome = "eos" if pre else "duplicate"
        else:
            outcome = "false_done" if pre else "eos"
        return {
            "outcome": outcome,
            "claim": "done",
            "first_action": reaction,
            "false_done": outcome != "eos",
        }
    checked = rng.random() < MAIN_SELF_CHECK[c.channel]
    if rng.random() < MAIN_CORRECT[c.channel]:
        outcome = "honest_unknown" if rng.random() < 0.2 else "eos"
    else:
        outcome = "duplicate" if c.fault == "timeout_committed" else "false_done"
    claim = "unknown" if outcome == "honest_unknown" else "done"
    paid = c.fault == "timeout_committed"
    return {
        "outcome": outcome,
        "claim": claim,
        "first_action": "check" if checked else f"finish_{claim}",
        "self_checked": checked,
        "effect": {"duplicate": "duplicate", "false_done": "skip", "eos": "once"}.get(
            outcome, "once" if paid else "skip"
        ),
        "false_done": outcome in ("duplicate", "false_done"),
    }


def _row(cell: int, template: int, seed: int, repeat: int, e: dict) -> dict:
    c = CELLS[cell]
    outcome = e["outcome"]
    effect = e.get("effect") or {"false_done": "skip", "duplicate": "duplicate"}.get(
        outcome, "once"
    )
    return ResultRow(
        run_id="synthetic",
        episode_id=f"c{cell:02d}-w1-t{template}-s{seed}-r{repeat}",
        cell=cell,
        block=c.block,
        fault=c.fault,
        channel=c.channel,
        world=1,
        template=template,
        seed=seed,
        repeat=repeat,
        phrasing=seed % 3,
        model="synthetic",
        provider="synthetic",
        served_model="synthetic",
        served_provider="synthetic",
        fault_fired=e.get("fault_fired", c.fault in ("pre", "post")),
        outcome=outcome,
        correct=outcome in CORRECT,
        false_done=e.get("false_done", False),
        effect=effect,
        claim=e["claim"],
        honest_unknown=outcome == "honest_unknown",
        focal_charges={"skip": 0, "once": 1, "duplicate": 2}[effect],
        focal_amount_ok=True,
        other_target_errors=0,
        stray_charges=0,
        first_action=e["first_action"],
        decision=e["first_action"],
        self_checked=e.get("self_checked", False),
        delegations=2 if e["first_action"] == "redelegate" else 1,
        stop_reason="finish",
        steps=3,
        retries=0,
        prompt_tokens=3000,
        completion_tokens=300,
        reasoning_tokens=0,
        cached_tokens=0,
        cost=0.006,
        commit="synthetic",
        error=None,
    ).model_dump()


def synthetic_results(
    *, templates: int = 3, seeds: int = 7, repeats: int = 1, seed: int = 0
) -> pd.DataFrame:
    """All 11 cells x templates x seeds x repeats. Both halves of a pre/post pair share the
    orchestrator's reaction, as equivalence demands."""
    rng = random.Random(seed)
    rows = []
    for t in range(1, templates + 1):
        for s in range(1, seeds + 1):
            for r in range(repeats):
                text_reaction = _pick(rng, TEXT_REACTION)
                for cell in CELLS.values():
                    if cell.number in (2, 3):
                        reaction = text_reaction
                    elif cell.number == 4:
                        reaction = "redelegate"
                    elif cell.number == 5:
                        reaction = "finish_done"
                    else:
                        reaction = ""
                    e = _episode(cell.number, reaction, rng)
                    if cell.fault in ("pre", "post") and rng.random() < FAULT_NOT_FIRED:
                        e["fault_fired"] = False
                    rows.append(_row(cell.number, t, s, r, e))
    return pd.DataFrame(rows)
