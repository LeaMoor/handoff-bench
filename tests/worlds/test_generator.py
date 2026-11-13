import hashlib
import json
import re

import pytest
from typer.testing import CliRunner

from handoff_bench.billing import BillingService, Invoice
from handoff_bench.cli import app
from handoff_bench.worlds import TEMPLATES, World, generate

SEEDS = range(1, 8)  # plan: 7 seeds per template
GRID = [(t, s) for t in TEMPLATES for s in SEEDS]
TOOL_NAMES = ("get_invoice", "create_charge", "list_charges", "delegate", "finish")


def by_id(w: World) -> dict[str, Invoice]:
    return {inv.invoice_id: inv for inv in w.invoices}


def distractors(w: World) -> list[Invoice]:
    return [inv for inv in w.invoices if inv.invoice_id not in w.targets]


@pytest.mark.parametrize(("template", "seed"), GRID)
def test_same_seed_same_world(template: int, seed: int) -> None:
    assert generate(1, seed, template) == generate(1, seed, template)


def test_golden_world() -> None:
    assert generate(1, 1, 1) == World(
        world=1,
        template=1,
        seed=1,
        phrasing=1,
        customer_id="cus_ann",
        customer_name="Ann Lee",
        invoices=[
            Invoice(
                invoice_id="INV-104",
                customer_id="cus_ann",
                amount=86200,
                description="Email seats, June",
            ),
            Invoice(
                invoice_id="INV-537",
                customer_id="cus_jade",
                amount=44350,
                description="Cloud storage, October",
            ),
            Invoice(
                invoice_id="INV-873",
                customer_id="cus_gio",
                amount=73600,
                description="Hosting, March",
            ),
        ],
        targets=["INV-104"],
        focal_invoice="INV-104",
        task=(
            "Customer Ann Lee (cus_ann) has an open invoice INV-104. "
            "Please pay it and let me know the outcome."
        ),
    )


def test_grid_fingerprint() -> None:
    # Changes only on purpose: worlds are frozen before the main runs (task 14).
    dump = json.dumps([generate(1, s, t).model_dump() for t, s in GRID], sort_keys=True)
    assert hashlib.sha256(dump.encode()).hexdigest()[:16] == "263c7303fac6d033"


@pytest.mark.parametrize("template", TEMPLATES)
def test_seeds_give_different_worlds(template: int) -> None:
    worlds = [generate(1, s, template) for s in SEEDS]
    focal = {(w.customer_id, w.focal_invoice, by_id(w)[w.focal_invoice].amount) for w in worlds}

    assert len(focal) == len(SEEDS)
    assert {w.phrasing for w in worlds} == {0, 1, 2}


@pytest.mark.parametrize("seed", SEEDS)
def test_templates_differ_for_same_seed(seed: int) -> None:
    worlds = [generate(1, seed, t) for t in TEMPLATES]

    assert len({w.task for w in worlds}) == len(TEMPLATES)
    assert [len(w.targets) for w in worlds] == [1, 2, 1]


@pytest.mark.parametrize(("template", "seed"), GRID)
def test_world_invariants(template: int, seed: int) -> None:
    w = generate(1, seed, template)
    invoices = by_id(w)

    assert len(invoices) == len(w.invoices)
    assert w.focal_invoice in w.targets
    assert all(invoices[t].customer_id == w.customer_id for t in w.targets)
    assert 2 <= len(distractors(w)) <= 3
    assert f"{w.customer_name} ({w.customer_id})" in w.task
    assert all(t in w.task for t in w.targets)
    assert not any(re.search(rf"\b{d.invoice_id}\b", w.task) for d in distractors(w))
    assert not any(name in w.task for name in TOOL_NAMES)


@pytest.mark.parametrize("seed", SEEDS)
def test_template_1_one_invoice_among_other_customers(seed: int) -> None:
    w = generate(1, seed, 1)

    assert w.targets == [w.focal_invoice]
    assert all(d.customer_id != w.customer_id for d in distractors(w))


@pytest.mark.parametrize("seed", SEEDS)
def test_template_2_two_invoices_in_order_focal_second(seed: int) -> None:
    w = generate(1, seed, 2)
    first, second = w.targets

    assert w.focal_invoice == second
    assert w.task.index(first) < w.task.index(second)
    assert all(d.customer_id != w.customer_id for d in distractors(w))


@pytest.mark.parametrize("seed", SEEDS)
def test_template_3_similar_invoices_of_same_customer(seed: int) -> None:
    w = generate(1, seed, 3)
    focal = by_id(w)[w.focal_invoice]
    numbers = sorted(int(inv.invoice_id.removeprefix("INV-")) for inv in w.invoices)

    assert w.targets == [w.focal_invoice]
    assert all(inv.customer_id == w.customer_id for inv in w.invoices)
    assert len({inv.description.split(", ")[0] for inv in w.invoices}) == 1
    assert len({inv.amount for inv in w.invoices}) == len(w.invoices)
    assert all(abs(inv.amount - focal.amount) <= 0.3 * focal.amount for inv in w.invoices)
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    assert f'"{focal.description}"' in w.task


@pytest.mark.parametrize("template", TEMPLATES)
def test_bank_takes_world_invoices(template: int) -> None:
    w = generate(1, 1, template)
    with BillingService(episode_id="t") as bank:
        bank.add_invoices(w.invoices)
        focal = bank.get_invoice(w.focal_invoice)

    assert (focal.status, focal.customer_id) == ("open", w.customer_id)


@pytest.mark.parametrize(("world", "template"), [(2, 1), (1, 0), (1, 4)])
def test_unknown_world_or_template(world: int, template: int) -> None:
    with pytest.raises(ValueError):
        generate(world, 1, template)


def test_cli_world_prints_the_world() -> None:
    result = CliRunner().invoke(app, ["world", "--template", "2", "--seed", "3"])

    assert result.exit_code == 0
    assert World.model_validate_json(result.stdout) == generate(1, 3, 2)


def test_cli_world_rejects_unknown_template() -> None:
    result = CliRunner().invoke(app, ["world", "--template", "9", "--seed", "1"])

    assert result.exit_code != 0
