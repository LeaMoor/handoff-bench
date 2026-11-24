"""B1: a read-only workflow inside the single agent's conversation."""

from typing import Any

from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.tools import ToolCallError

REVIEW_TOOL = "review_workflow"
REVIEW_PROMPT = (
    " After a payment times out, call review_workflow in two separate turns. "
    "The first call opens the internal review. The second retrieves the review result. "
    "Then continue handling the payment as you see fit. "
    "The workflow service does not have access to the bank ledger."
)


def register_review(gateway: ToolGateway, *, dirty: bool) -> None:
    """Register two local review steps and inject a read error in the dirty condition."""
    opened = False

    async def review(_actor: str, _args: dict[str, Any]) -> str:
        nonlocal opened
        if not opened:
            opened = True
            return "The internal review is open. Call review_workflow to retrieve the result."
        if dirty:
            raise ToolCallError("review_unavailable", "The internal review result could not load.")
        return "The internal review is complete. Continue handling the user's request."

    gateway.register(
        REVIEW_TOOL,
        review,
        description="Open an internal review or retrieve its result. This tool is read-only.",
    )
