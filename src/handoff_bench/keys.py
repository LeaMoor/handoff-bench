"""Who sets the idempotency key of a payment (M4, plan step 8).

none         no key: the field is hidden from the subagent and stripped from its calls
subagent     whatever the subagent passes (the default everywhere else)
orchestrator the orchestrator is asked to put a key into the goal; the subagent may keep it
task         the harness sets the key from the task id: a respawned subagent gets a new task id
intent       the harness sets the key from the invoice and customer (the meaning of the payment)
pinned       the harness pins the first charge of an intent and replays its result on repeats
"""

from collections.abc import Mapping
from dataclasses import replace

from handoff_bench.agents.gateway import CallHook, PendingCall
from handoff_bench.agents.tools import ToolResult
from handoff_bench.cells import KeySource

KEY_FIELD = "idempotency_key"
ORCHESTRATOR_KEY_PROMPT = (
    " When you delegate a payment, make up an idempotency key for it, write it into the goal "
    "as `idempotency_key: <key>` and ask the subagent to pass exactly that key to create_charge."
)


def intent_key(args: Mapping[str, object]) -> str:
    return f"intent:{args.get('invoice_id')}:{args.get('customer_id')}"


class KeyHook(CallHook):
    """`tasks` maps a subagent (`sub-N`) to the task it runs; Delegation fills it."""

    def __init__(self, source: KeySource, tasks: Mapping[str, str]) -> None:
        self.source = source
        self.tasks = tasks
        self.pinned: dict[str, ToolResult] = {}

    def rewrite(self, call: PendingCall) -> PendingCall:
        if call.tool != "create_charge" or self.source in ("subagent", "orchestrator"):
            return call
        args = {k: v for k, v in call.args.items() if k != KEY_FIELD}
        if self.source == "task":
            args[KEY_FIELD] = f"task:{self.tasks.get(call.actor, call.actor)}"
        elif self.source in ("intent", "pinned"):
            args[KEY_FIELD] = intent_key(args)
        return replace(call, args=args)

    async def before_call(self, call: PendingCall) -> ToolResult | None:
        if self.source == "pinned" and call.tool == "create_charge":
            return self.pinned.get(intent_key(call.args))
        return None

    async def after_call(self, call: PendingCall, result: ToolResult) -> ToolResult:
        if self.source == "pinned" and call.tool == "create_charge" and result.outcome == "ok":
            self.pinned.setdefault(intent_key(call.args), result)
        return result
