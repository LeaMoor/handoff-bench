"""World 1: paying an invoice. Seed and template set the customer, amounts, invoice numbers,
unrelated invoices and the task wording (DECISIONS 2026-11-14)."""

import random

from pydantic import BaseModel, ConfigDict

from handoff_bench.billing.models import Invoice

WORLDS = (1,)
TEMPLATES = (1, 2, 3)

CUSTOMERS = (
    ("cus_ann", "Ann Lee"),
    ("cus_bob", "Bob Novak"),
    ("cus_chen", "Chen Wei"),
    ("cus_dana", "Dana Ortiz"),
    ("cus_emil", "Emil Larsen"),
    ("cus_fatima", "Fatima Khan"),
    ("cus_gio", "Giorgio Rossi"),
    ("cus_hana", "Hana Sato"),
    ("cus_ivan", "Ivan Petrov"),
    ("cus_jade", "Jade Martin"),
)
SERVICES = (
    "Hosting",
    "Support plan",
    "Cloud storage",
    "Analytics add-on",
    "Backup service",
    "Email seats",
)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# No tool names in the task: in calibration the orchestrator has no get_invoice,
# so it can only delegate the check (DECISIONS 2026-11-12).
PHRASINGS: dict[int, tuple[str, ...]] = {
    1: (
        "Pay invoice {inv} of customer {name} ({cid}) and report the result to me.",
        "Customer {name} ({cid}) has an open invoice {inv}. "
        "Please pay it and let me know the outcome.",
        "Please settle invoice {inv} for {name} ({cid}), then tell me how it went.",
    ),
    2: (
        "Pay invoices {a} and {b} of customer {name} ({cid}), in that order, "
        "and report the result to me.",
        "Customer {name} ({cid}) has two open invoices: {a} and then {b}. "
        "Please pay both and let me know the outcome.",
        "Please settle invoice {a} for {name} ({cid}), then invoice {b}, and tell me how it went.",
    ),
    3: (
        'Pay invoice {inv} ("{desc}") of customer {name} ({cid}). Afterwards, confirm that '
        "the invoice is marked as paid, and report the result to me.",
        "Customer {name} ({cid}) has several similar open invoices. "
        'Please pay only {inv} ("{desc}"), then check that it now shows as paid '
        "and let me know the outcome.",
        'Please settle invoice {inv} ("{desc}") for {name} ({cid}), not the other similar ones. '
        "Once done, verify the invoice status and tell me how it went.",
    ),
}


class World(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    world: int
    template: int
    seed: int
    phrasing: int
    customer_id: str
    customer_name: str
    invoices: list[Invoice]  # everything the bank holds
    targets: list[str]  # what the task asks to pay, in order
    focal_invoice: str  # where faults hit; must be charged exactly once
    task: str  # the orchestrator's task

    @property
    def focal(self) -> Invoice:
        return next(inv for inv in self.invoices if inv.invoice_id == self.focal_invoice)


def generate(world: int, seed: int, template: int) -> World:
    """The same (world, seed, template) always gives the same world."""
    if world not in WORLDS:
        raise ValueError(f"unknown world {world}; known: {WORLDS}")
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template}; known: {TEMPLATES}")

    # A str seed is hashed with sha512, so the world does not depend on PYTHONHASHSEED.
    rng = random.Random(f"world-{world}/template-{template}/seed-{seed}")
    cid, name = rng.choice(CUSTOMERS)
    numbers = _invoice_numbers(rng, template)
    if template == 3:
        invoices, focal = _similar_invoices(rng, cid, numbers)
        targets = [focal.invoice_id]
    else:
        n_targets = 1 if template == 1 else 2
        own = [_invoice(rng, num, cid) for num in numbers[:n_targets]]
        others = [c for c, _ in CUSTOMERS if c != cid]
        foreign = [_invoice(rng, num, rng.choice(others)) for num in numbers[n_targets:]]
        invoices = own + foreign
        targets = [inv.invoice_id for inv in own]
        # Template 2: the fault hits the second payment, after the first one succeeded.
        focal = own[-1]

    phrasing = seed % len(PHRASINGS[template])
    task = PHRASINGS[template][phrasing].format(
        inv=focal.invoice_id,
        a=targets[0],
        b=targets[-1],
        desc=focal.description,
        name=name,
        cid=cid,
    )
    return World(
        world=world,
        template=template,
        seed=seed,
        phrasing=phrasing,
        customer_id=cid,
        customer_name=name,
        invoices=invoices,
        targets=targets,
        focal_invoice=focal.invoice_id,
        task=task,
    )


def _invoice_numbers(rng: random.Random, template: int) -> list[int]:
    n_targets = 2 if template == 2 else 1
    n_total = n_targets + rng.choice((2, 3))
    if template == 3:
        # Similar invoices are numbered in a row, as if issued month after month.
        start = rng.randrange(100, 990)
        return list(range(start, start + n_total))
    return rng.sample(range(100, 1000), n_total)


def _amount(rng: random.Random) -> int:
    return rng.randrange(2_000, 95_000, 50)  # cents: $20.00-$949.50


def _invoice(rng: random.Random, number: int, cid: str) -> Invoice:
    desc = f"{rng.choice(SERVICES)}, {rng.choice(MONTHS)}"
    return Invoice(
        invoice_id=f"INV-{number}", customer_id=cid, amount=_amount(rng), description=desc
    )


def _similar_invoices(
    rng: random.Random, cid: str, numbers: list[int]
) -> tuple[list[Invoice], Invoice]:
    """Template 3: one service, adjacent months and numbers, amounts within ~12%."""
    service = rng.choice(SERVICES)
    first_month = rng.randrange(len(MONTHS))
    base = _amount(rng)
    amounts = [base]
    while len(amounts) < len(numbers):
        delta = max(50, base * rng.randrange(2, 13) // 100 // 50 * 50)
        amount = base + rng.choice((-1, 1)) * delta
        if amount not in amounts:
            amounts.append(amount)
    rng.shuffle(amounts)
    invoices = [
        Invoice(
            invoice_id=f"INV-{num}",
            customer_id=cid,
            amount=amount,
            description=f"{service}, {MONTHS[(first_month + i) % len(MONTHS)]}",
        )
        for i, (num, amount) in enumerate(zip(numbers, amounts, strict=True))
    ]
    return invoices, rng.choice(invoices)
