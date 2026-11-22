from pydantic import BaseModel

from handoff_bench.agents.gateway import CallHook, PendingCall
from handoff_bench.agents.tools import ToolResult
from handoff_bench.billing import EffectJournal


class PartialStop(BaseModel):
    actor: str
    call_seq: int
    charge_id: str
    bank_step: int
    invoice_id: str


class PartialExecution(CallHook):
    """Stop the first child that commits a target payment after k distinct target commits."""

    def __init__(self, targets: list[str], threshold: int, effects: EffectJournal) -> None:
        self._targets = set(targets)
        self._threshold = threshold
        self._effects = effects
        self._effect_count = len(effects.rows)
        self._payer: str | None = None
        self._charged: set[str] = set()
        self.stop: PartialStop | None = None

    @property
    def fired(self) -> bool:
        return self.stop is not None

    @property
    def fired_on(self) -> str | None:
        return self.stop.actor if self.stop is not None else None

    def report_lost(self, actor: str) -> bool:
        return self.stop is not None and self.stop.actor == actor

    async def after_call(self, call: PendingCall, result: ToolResult) -> ToolResult:
        if self.fired or call.tool != "create_charge" or not call.actor.startswith("sub-"):
            return result
        # The gateway runs bank calls serially. A replay returns the old charge without
        # appending an effect, even if it immediately follows the original call.
        rows = self._effects.rows
        new_effect = len(rows) > self._effect_count
        self._effect_count = len(rows)
        if result.outcome != "ok" or not new_effect:
            return result
        effect = rows[-1]
        if effect.actor != call.actor or effect.invoice_id not in self._targets:
            return result
        if self._payer is None:
            self._payer = call.actor
        if call.actor != self._payer:
            return result
        self._charged.add(effect.invoice_id)
        if len(self._charged) < self._threshold:
            return result
        self.stop = PartialStop(
            actor=call.actor,
            call_seq=call.seq,
            charge_id=effect.charge_id,
            bank_step=effect.step,
            invoice_id=effect.invoice_id,
        )
        return result.model_copy(update={"outcome": "dropped"})
