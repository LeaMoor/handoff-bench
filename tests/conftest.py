from collections.abc import Callable, Iterator
from contextlib import ExitStack

import pytest

from handoff_bench.billing import BillingService
from handoff_bench.billing.server import running_server

ServeBank = Callable[[BillingService], str]


@pytest.fixture(autouse=True)
def no_paid_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    # The CLI loads .env; an empty key wins over it, so a real client fails instead of paying.
    monkeypatch.setenv("OPENROUTER_API_KEY", "")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def serve_bank() -> Iterator[ServeBank]:
    with ExitStack() as stack:
        yield lambda service: stack.enter_context(running_server(service))
