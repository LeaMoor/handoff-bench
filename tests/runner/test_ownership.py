"""Exercise the M8 cells through the runner and the real MCP billing gateway."""

import csv
import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from helpers.episode import report
from helpers.fake_model import FakeModel, Step

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.loop import ModelCallLog, rebuild_requests
from handoff_bench.agents.model_client import ModelResponse
from handoff_bench.billing import EffectJournal
from handoff_bench.cells import CELLS
from handoff_bench.jsonl import read_rows
from handoff_bench.runner.batch import BatchConfig
from handoff_bench.runner.episode import EpisodeConfig
from handoff_bench.runner.results import ResultsCsv
from handoff_bench.runner.run import run_one
from handoff_bench.worlds import World, generate

pytestmark = pytest.mark.anyio


def test_ownership_batch_uses_explicit_cell_numbers() -> None:
    config_path = Path(__file__).resolve().parents[2] / "configs/prefreeze/haiku-ownership.yaml"
    batch = BatchConfig.load(config_path)

    assert batch.cells == [74, 75, 76, 94]
    assert [(cell.number, cell.ownership) for cell in CELLS.values() if cell.ownership] == [
        (74, "disjoint"),
        (75, "overlap"),
        (76, "d12"),
        (94, "d12_owner"),
    ]
    assert [(episode.cell, episode.ownership) for episode in batch.episodes("test")[:4]] == [
        (74, "disjoint"),
        (75, "overlap"),
        (76, "d12"),
        (94, "d12_owner"),
    ]


def charge(world: World, invoice_id: str) -> Step:
    invoice = next(i for i in world.invoices if i.invoice_id == invoice_id)
    return [
        (
            "create_charge",
            {
                "invoice_id": invoice_id,
                "customer_id": invoice.customer_id,
                "amount": invoice.amount,
                "idempotency_key": "ignored",
            },
        )
    ]


class PairedModel:
    def __init__(
        self, world: World, *, disjoint: bool, non_owner_first: bool, owner_skips: bool
    ) -> None:
        a, shared, b = world.targets
        self.a = a
        self.both_started = anyio.Event()
        self.first_write_finished = anyio.Event()
        self.b_reporting = anyio.Event()
        self.started: set[str] = set()
        self.first_writer = "B" if non_owner_first else "A"
        self.race = not disjoint and not owner_skips
        self.parent = FakeModel(
            [
                [("delegate_parallel", {})],
                [("delegate_parallel", {})],
                [
                    (
                        "finish",
                        {"status": "done", "done": world.targets, "uncertain": [], "message": ""},
                    )
                ],
            ]
        )
        check: Step = [("get_invoice", {"invoice_id": shared})]
        off_target = next(i.invoice_id for i in world.invoices if i.invoice_id not in world.targets)
        owner_work = [] if owner_skips else [charge(world, shared)]
        peer_work = [] if disjoint else [charge(world, shared)]
        if owner_skips:
            peer_work.append(charge(world, off_target))
        self.children = {
            "A": FakeModel([check, *owner_work, charge(world, a), report(text="report A")]),
            "B": FakeModel([check, *peer_work, charge(world, b), report(text="report B")]),
        }

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        if any(t["function"]["name"] == "delegate_parallel" for t in tools):
            return await self.parent.complete(messages, tools)

        worker = "A" if self.a in messages[1]["content"] else "B"
        model = self.children[worker]
        match len(model.requests):
            case 0:
                self.started.add(worker)
                if len(self.started) == 2:
                    self.both_started.set()
                await self.both_started.wait()
            case 1 if self.race and worker != self.first_writer:
                await self.first_write_finished.wait()
            case 2 if self.race and worker == self.first_writer:
                self.first_write_finished.set()
        if len(model.script) == 1 and self.first_writer == "B":
            if worker == "B":
                self.b_reporting.set()
            else:
                await self.b_reporting.wait()
        return await model.complete(messages, tools)


@pytest.mark.parametrize(
    ("cell", "non_owner_first", "owner_skips", "shared_commits", "denials"),
    [
        (74, False, False, ["sub-1"], []),
        (75, True, False, ["sub-2", "sub-1"], []),
        (76, False, False, ["sub-1"], ["sub-2"]),
        (76, True, False, ["sub-1"], ["sub-2"]),
        (76, False, True, [], ["sub-2"]),
        (94, True, False, ["sub-1"], ["sub-2"]),
    ],
)
async def test_parallel_worklists(
    tmp_path: Path,
    cell: int,
    non_owner_first: bool,
    owner_skips: bool,
    shared_commits: list[str],
    denials: list[str],
) -> None:
    config = EpisodeConfig(cell=cell, template=4, seed=1)
    world = generate(1, 1, 4)
    a, shared, b = world.targets
    model = PairedModel(
        world, disjoint=cell == 74, non_owner_first=non_owner_first, owner_skips=owner_skips
    )
    with anyio.fail_after(5):
        row = await run_one(config, model, run_dir=tmp_path, run_id="test")

    episode = tmp_path / config.episode_id
    settings = json.loads((episode / "config.json").read_text())
    trace = json.loads((episode / "trace.json").read_text())
    assert settings["worklists"] == {
        "work-a": [a, shared],
        "work-b": [b] if cell == 74 else [shared, b],
    }
    assert settings["owners"] == (
        {a: "work-a", shared: "work-a", b: "work-b"} if cell in (76, 94) else None
    )
    assert settings["bank"]["timeout"] is None
    assert trace["tasks"] == {"sub-1": "work-a", "sub-2": "work-b"}
    assert [s["actor"] for s in trace["subagents"]] == ["sub-1", "sub-2"]
    assert model.started == {"A", "B"}
    assert row.stop_reason == "finish"
    assert not row.fault_fired
    assert row.sub_claim == "none"
    assert row.delegations == 2

    calls = sorted(CallJournal.read(episode / "calls.jsonl"), key=lambda c: c.seq)
    assert len({call.seq for call in calls}) == len(calls)
    assert [c.error_code for c in calls if c.tool == "delegate_parallel"] == [
        None,
        "already_delegated",
    ]
    assert json.loads(model.parent.requests[1][-1]["content"])["reports"] == [
        {"actor": "sub-1", "task_id": "work-a", "result": "report A"},
        {"actor": "sub-2", "task_id": "work-b", "result": "report B"},
    ]
    assert {c.actor for c in calls if c.tool == "get_invoice" and c.outcome == "ok"} == {
        "sub-1",
        "sub-2",
    }
    assert all(
        "idempotency_key" not in tool["function"]["parameters"]["properties"]
        for child in model.children.values()
        for tool in child.tools[0]
        if tool["function"]["name"] == "create_charge"
    )

    effects = EffectJournal.read(episode / "effects.jsonl")
    assert sorted((e.invoice_id, e.actor) for e in effects if e.invoice_id != shared) == sorted(
        [(a, "sub-1"), (b, "sub-2")]
    )
    assert all(e.idempotency_key is None for e in effects)
    assert [e.actor for e in effects if e.invoice_id == shared] == shared_commits
    shared_attempts = [
        c for c in calls if c.tool == "create_charge" and c.args["invoice_id"] == shared
    ]
    if non_owner_first:
        assert [c.actor for c in shared_attempts] == ["sub-2", "sub-1"]
        assert [c.actor for c in calls if c.tool == "finish_report"] == ["sub-2", "sub-1"]
    assert [c.actor for c in shared_attempts if c.error_code == "ownership_denied"] == denials
    assert all(
        c.outcome == "rejected" for c in shared_attempts if c.error_code == "ownership_denied"
    )

    for call in shared_attempts:
        if call.error_code == "ownership_denied":
            assert ("owned by work-a; do not retry" in (call.error_message or "")) == (cell == 94)

    result = row.ownership
    assert result is not None
    assert result.concurrent_children
    assert result.records[shared].attempted_by_multiple_children == (cell != 74 and not owner_skips)
    assert result.records[shared].charge_count == len(shared_commits)
    assert result.records[shared].charged_amount == next(
        i.amount for i in world.invoices if i.invoice_id == shared
    ) * len(shared_commits)
    assert result.skipped_invoices == ([shared] if owner_skips else [])
    assert result.duplicated_invoices == ([shared] if cell == 75 else [])
    assert result.off_target_charges == 0
    assert result.parent.status_mismatch == (cell == 75 or owner_skips)
    assert result.parent.incorrect_done == ([shared] if cell == 75 or owner_skips else [])
    assert row.correct == (cell != 75 and not owner_skips)
    if owner_skips:
        (foreign,) = (
            record for record in result.records.values() if record.expected_amount is None
        )
        assert foreign.denied_by == ["sub-2"]
        assert foreign.charge_count == 0

    requests = rebuild_requests(read_rows(episode / "model_calls.jsonl", ModelCallLog))
    assert requests["sub-1", 1][1] == model.children["A"].requests[0][1]
    assert requests["sub-2", 1][1] == model.children["B"].requests[0][1]
    results = ResultsCsv(tmp_path / "results.csv")
    results.append(row)
    with results.path.open() as f:
        (saved,) = csv.DictReader(f)
    assert json.loads(saved["ownership"]) == result.model_dump()


async def test_child_failure_cancels_and_joins_sibling(tmp_path: Path) -> None:
    config = EpisodeConfig(cell=76, template=4, seed=1)
    world = generate(1, 1, 4)
    sibling_started = anyio.Event()
    sibling_stopped = anyio.Event()
    parent = FakeModel([[("delegate_parallel", {})]])

    class FailingModel:
        async def complete(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelResponse:
            if any(t["function"]["name"] == "delegate_parallel" for t in tools):
                return await parent.complete(messages, tools)
            if world.targets[0] in messages[1]["content"]:
                await sibling_started.wait()
                raise RuntimeError("scripted child failure")
            try:
                sibling_started.set()
                await anyio.sleep_forever()
            finally:
                sibling_stopped.set()

    failure = pytest.RaisesExc(RuntimeError, match="scripted child failure")
    with anyio.fail_after(5), pytest.RaisesGroup(failure, flatten_subgroups=True):
        await run_one(config, FailingModel(), run_dir=tmp_path, run_id="test")
    assert sibling_stopped.is_set()
