from collections.abc import Callable

import pytest

from handoff_bench.billing import BankConfig, BillingService, Invoice

INV = Invoice(invoice_id="INV-12", customer_id="cus_ann", amount=12000, description="Hosting")
OTHER = Invoice(invoice_id="INV-13", customer_id="cus_bob", amount=4500)

MakeBank = Callable[..., BillingService]


@pytest.fixture
def make_bank() -> MakeBank:
    def make(config: BankConfig | None = None, id_seed: int = 0) -> BillingService:
        bank = BillingService(episode_id="ep-test", config=config, id_seed=id_seed)
        bank.add_invoices([INV, OTHER])
        return bank

    return make


@pytest.fixture
def bank(make_bank: MakeBank) -> BillingService:
    return make_bank()
