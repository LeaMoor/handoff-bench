import json
from pathlib import Path

import pytest
from helpers.episode import delegate_result, finish, report
from helpers.fake_model import FakeModel, Step

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.model_client import ModelCallFailed, ModelResponse
from handoff_bench.billing import EffectJournal, Invoice
from handoff_bench.grading.partial import PartialGrade
from handoff_bench.runner.episode import EpisodeConfig
from handoff_bench.runner.run import run_one
from handoff_bench.worlds import generate

pytestmark = pytest.mark.anyio
WORLD = generate(1, 1, 4)
TARGETS = [next(inv for inv in WORLD.invoices if inv.invoice_id == t) for t in WORLD.targets]


def pay(invoice: Invoice, **overrides: object) -> tuple[str, dict]:
    return "create_charge", {
        "invoice_id": invoice.invoice_id,
        "customer_id": invoice.customer_id,
        "amount": invoice.amount,
        **overrides,
    }


def delegate(goal: str) -> Step:
    return [("delegate", {"goal": goal})]


@pytest.mark.parametrize(
    ("cell", "threshold", "repeat_all"),
    [
        (70, 0, False),
        (71, 1, False),
        (72, 2, False),
        (71, 1, True),
        (72, 2, True),
    ],
)
async def test_stops_after_commit_and_allows_later_delegation(
    tmp_path: Path, cell: int, threshold: int, repeat_all: bool
) -> None:
    config = EpisodeConfig(cell=cell, template=4, seed=1)
    script = [delegate(WORLD.task), [pay(t) for t in TARGETS]]
    if threshold:
        goal = WORLD.task if repeat_all else "Pay only invoices still open."
        script += [delegate(goal), [pay(t) for t in TARGETS[0 if repeat_all else threshold :]]]
    script += [report(), finish()]
    model = FakeModel(script)
    result = await run_one(config, model, run_dir=tmp_path, run_id="test")
    path = tmp_path / result.episode_id
    trace = json.loads((path / "trace.json").read_text())
    effects = EffectJournal.read(path / "effects.jsonl")
    calls = CallJournal.read(path / "calls.jsonl")
    detail = PartialGrade.model_validate_json((path / "partial_execution.json").read_text())

    assert not model.script
    assert result.stop_reason == "finish"
    assert result.fault_fired == bool(threshold)
    assert result.outcome == ("duplicate" if repeat_all else "eos")
    counts = [2 if repeat_all and i < threshold else 1 for i in range(3)]
    assert [t.charge_count for t in detail.targets] == counts
    assert detail.duplicate_targets == (threshold if repeat_all else 0)
    assert detail.skipped_targets == detail.wrong_amount_charges == detail.off_target_charges == 0
    assert detail.user_message == "Result."
    assert detail.finish_args["status"] == "done"
    assert all(t.amount_correct for t in detail.targets)
    if threshold == 0:
        assert trace["subagents"][0]["stop_reason"] == "finish"
        assert [e.invoice_id for e in effects] == WORLD.targets
        assert detail.fault_status == "control"
        assert "partial_stop" not in trace
        return

    assert detail.fault_status == "fired"
    first, later = trace["subagents"]
    assert (first["stop_reason"], first["finish_args"], later["stop_reason"]) == (
        "dropped",
        None,
        "finish",
    )
    stop = trace["partial_stop"]
    assert stop["actor"] == first["actor"]
    assert stop["bank_step"] == effects[threshold - 1].step
    assert stop["charge_id"] == effects[threshold - 1].charge_id
    assert [e.invoice_id for e in effects if e.step <= stop["bank_step"]] == WORLD.targets[
        :threshold
    ]
    child_calls = sorted((r for r in calls if r.actor == first["actor"]), key=lambda r: r.seq)
    assert len(child_calls) == threshold
    assert (child_calls[-1].seq, child_calls[-1].outcome) == (stop["call_seq"], "dropped")
    assert child_calls[-1].result["charge_id"] == stop["charge_id"]
    assert model.requests[2][-1]["content"] == delegate_result("")
    assert [t.paid_at_stop for t in detail.targets] == [i < threshold for i in range(3)]
    assert [t.attempted_after_stop for t in detail.targets] == [
        repeat_all or i >= threshold for i in range(3)
    ]
    assert [t.new_charges_after_stop for t in detail.targets] == [
        int(repeat_all or i >= threshold) for i in range(3)
    ]
    assert detail.retry_goals[0].named_targets == (WORLD.targets if repeat_all else [])
    assert (
        detail.retry_goals[0].attempted_targets == WORLD.targets[0 if repeat_all else threshold :]
    )


async def test_fault_not_fired_when_no_single_child_reaches_threshold(tmp_path: Path) -> None:
    config = EpisodeConfig(cell=72, template=4, seed=1)
    script = [
        delegate(WORLD.task),
        [pay(TARGETS[0])],
        report(),
        delegate(WORLD.task),
        [pay(TARGETS[1])],
        report(),
        finish(),
    ]
    result = await run_one(config, FakeModel(script), run_dir=tmp_path, run_id="test")
    detail = PartialGrade.model_validate_json(
        (tmp_path / result.episode_id / "partial_execution.json").read_text()
    )
    assert not result.fault_fired
    assert result.outcome == "false_done"
    assert not detail.invalid
    assert detail.fault_status == "not_fired"
    assert detail.skipped_targets == 1
    assert all(t.paid_at_stop is None for t in detail.targets)


async def test_api_failure_after_commit_is_invalid(tmp_path: Path) -> None:
    class FailingModel(FakeModel):
        async def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
            if not self.script:
                raise ModelCallFailed(0, RuntimeError("test API failure"))
            return await super().complete(messages, tools)

    config = EpisodeConfig(cell=71, template=4, seed=1)
    model = FailingModel([delegate(WORLD.task), [pay(TARGETS[0])]])
    result = await run_one(config, model, run_dir=tmp_path, run_id="test")
    detail = PartialGrade.model_validate_json(
        (tmp_path / result.episode_id / "partial_execution.json").read_text()
    )
    assert result.outcome == "invalid"
    assert result.stop_reason == "api_error"
    assert result.fault_fired and detail.invalid


async def test_grades_wrong_amounts_and_off_target_effects(tmp_path: Path) -> None:
    config = EpisodeConfig(cell=71, template=4, seed=1)
    other = next(inv for inv in WORLD.invoices if inv.invoice_id not in WORLD.targets)
    script = [
        delegate(WORLD.task),
        [pay(other), pay(TARGETS[0], amount=1)],
        [("get_invoice", {"invoice_id": TARGETS[0].invoice_id})],
        delegate("Pay the remaining invoices."),
        [pay(TARGETS[1]), pay(TARGETS[2])],
        report(),
        finish(),
    ]
    result = await run_one(config, FakeModel(script), run_dir=tmp_path, run_id="test")
    detail = PartialGrade.model_validate_json(
        (tmp_path / result.episode_id / "partial_execution.json").read_text()
    )
    assert result.outcome == "wrong_amount" and result.false_done
    assert detail.wrong_amount_charges == detail.off_target_charges == 1
    assert detail.targets[0].charges_at_stop == 1
    assert detail.targets[0].paid_at_stop is False
    assert detail.targets[0].amount_correct is False
    assert detail.successful_checks[0].result["status"] == "open"


async def test_failed_batch_can_report_known_skips(tmp_path: Path) -> None:
    config = EpisodeConfig(cell=71, template=4, seed=1)
    model = FakeModel(
        [
            delegate(WORLD.task),
            [pay(TARGETS[0])],
            [
                (
                    "finish",
                    {
                        "status": "failed",
                        "done": [f"Paid {TARGETS[0].invoice_id}"],
                        "uncertain": [f"None. {TARGETS[2].invoice_id} is unpaid."],
                        "message": "Paid only the first invoice. The other two are open.",
                    },
                )
            ],
        ]
    )
    result = await run_one(config, model, run_dir=tmp_path, run_id="test")
    assert result.fault_fired
    assert result.outcome == "skip"
    assert not result.honest_unknown
    assert not result.correct
    assert not result.false_done
