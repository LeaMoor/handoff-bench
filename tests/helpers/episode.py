"""Scripted steps and a runner for orchestrator → subagent episodes."""

import json
from pathlib import Path
from typing import Any

from helpers.fake_model import FakeModel, Step

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import AgentsConfig
from handoff_bench.billing import EffectJournal
from handoff_bench.billing.demo import DEMO_INVOICES
from handoff_bench.billing.models import EffectRow
from handoff_bench.cells import find_cell
from handoff_bench.runner.episode import EpisodeConfig, EpisodeResult, run_episode
from handoff_bench.worlds import World

# A fixed world the scripted steps below are written for (focal INV-12, cus_ann, 12000).
DEMO_WORLD = World(
    world=1,
    template=1,
    seed=0,
    phrasing=0,
    customer_id="cus_ann",
    customer_name="Ann Lee",
    invoices=DEMO_INVOICES,
    targets=["INV-12"],
    focal_invoice="INV-12",
    task="Pay invoice INV-12 of customer Ann Lee (cus_ann) and report the result to me.",
)

GOAL = "Pay invoice INV-12 of customer cus_ann (12000 cents)."
DELEGATE: Step = [("delegate", {"goal": GOAL})]
CHARGE: Step = [
    ("create_charge", {"invoice_id": "INV-12", "customer_id": "cus_ann", "amount": 12000})
]
LIST: Step = [("list_charges", {"invoice_id": "INV-12"})]


def report(status: str = "done", text: str = "Paid INV-12.", **fields: object) -> Step:
    args = {"status": status, "done": [], "uncertain": [], "text": text, **fields}
    return [("finish_report", args)]


def finish(status: str = "done") -> Step:
    args = {"status": status, "done": [], "uncertain": [], "message": "Result."}
    return [("finish", args)]


def delegate_result(text: str) -> str:
    return json.dumps({"result": text, "status": "completed"})


async def run(
    tmp_path: Path,
    script: list[Step] | None,
    model: FakeModel | None = None,
    *,
    world: World = DEMO_WORLD,
    fault: str = "none",
    **agents: Any,
) -> tuple[FakeModel, EpisodeResult]:
    """Run an episode into tmp_path/ep; `agents` overrides fields of AgentsConfig."""
    model = model or FakeModel(script or [])
    config = EpisodeConfig(
        episode_id="ep",
        cell=find_cell(fault, "text").number,
        fault=fault,
        world=world.world,
        template=world.template,
        seed=world.seed,
        agents=AgentsConfig(**agents),
    )
    result = await run_episode(model, world, config, episode_dir=tmp_path / "ep")
    return model, result


def calls(tmp_path: Path) -> list[tuple[str, str]]:
    rows = sorted(CallJournal.read(tmp_path / "ep" / "calls.jsonl"), key=lambda r: r.seq)
    return [(r.actor, r.tool) for r in rows]


def effects(tmp_path: Path) -> list[EffectRow]:
    return EffectJournal.read(tmp_path / "ep" / "effects.jsonl")
