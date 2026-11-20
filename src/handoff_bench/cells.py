from typing import Literal

from pydantic import BaseModel, ConfigDict

Block = Literal["control", "calibration", "main", "keys", "processing"]
Fault = Literal[
    "none",
    "pre",
    "post",
    "timeout_lost",
    "timeout_committed",
    "respawn",
    "redeliver",
    "processing_settles",
    "processing_fails",
]
KeySource = Literal["none", "subagent", "orchestrator", "task", "intent", "pinned"]
# code_key: the statement plus the idempotency key of each charge; none: a single agent.
Channel = Literal["text", "struct", "code", "code_key", "none"]
Delay = Literal["short", "long"]
Agent = Literal["delegated", "single"]


class Cell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int
    block: Block
    fault: Fault
    channel: Channel
    # long: the charge that got a timeout stays invisible to checks for the whole episode.
    delay: Delay = "short"
    rules: bool = True  # the subagent gets the rules block
    agent: Agent = "delegated"
    key: KeySource = "subagent"  # who sets the idempotency key (M4)


_TIMEOUTS: tuple[Fault, ...] = ("timeout_lost", "timeout_committed")
_MAIN = [(fault, channel) for channel in ("text", "struct", "code") for fault in _TIMEOUTS]

_cells = [
    Cell(number=1, block="control", fault="none", channel="text"),
    Cell(number=2, block="calibration", fault="pre", channel="text"),
    Cell(number=3, block="calibration", fault="post", channel="text"),
    Cell(number=4, block="calibration", fault="pre", channel="code"),
    Cell(number=5, block="calibration", fault="post", channel="code"),
]
# Main block: cells 6-11 short delay with rules (the plan), then 12-17 short without rules,
# 18-23 long with rules, 24-29 long without rules; each six = channel x world.
for delay, rules in (("short", True), ("short", False), ("long", True), ("long", False)):
    for fault, channel in _MAIN:
        _cells.append(
            Cell(
                number=len(_cells) + 1,
                block="main",
                fault=fault,
                channel=channel,
                delay=delay,
                rules=rules,
            )
        )
# B0, cells 30-31: the same fault with long delay, but the orchestrator pays itself.
for fault in _TIMEOUTS:
    _cells.append(
        Cell(
            number=len(_cells) + 1,
            block="main",
            fault=fault,
            channel="none",
            delay="long",
            rules=False,
            agent="single",
        )
    )
# Calibration without the loophole, cells 32-35: like 2-5, but the focal charge never shows.
for fault, channel in (("pre", "text"), ("post", "text"), ("pre", "code"), ("post", "code")):
    _cells.append(
        Cell(
            number=len(_cells) + 1,
            block="calibration",
            fault=fault,
            channel=channel,
            delay="long",
        )
    )
# M4: the system repeats the work itself, so the key decides, not the orchestrator.
# respawn: the report is lost after the charge and the harness starts a new subagent (new task);
# redeliver: the same delegation is delivered twice (same task).
# Cells 36-47: a check can see the first charge; 48-59: it cannot (only the key prevents a repeat).
for delay in ("short", "long"):
    for fault in ("respawn", "redeliver"):
        for key in ("none", "subagent", "orchestrator", "task", "intent", "pinned"):
            _cells.append(
                Cell(
                    number=len(_cells) + 1,
                    block="keys",
                    fault=fault,
                    channel="text",
                    delay=delay,
                    key=key,
                )
            )
# Cells 60-63: like 34-35 and 28-29, but the statement shows the charge's key — does the
# orchestrator pass a key it can see on to the next subagent?
for block, fault, rules in (
    ("calibration", "pre", True),
    ("calibration", "post", True),
    ("main", "timeout_lost", False),
    ("main", "timeout_committed", False),
):
    _cells.append(
        Cell(
            number=len(_cells) + 1,
            block=block,
            fault=fault,
            channel="code_key",
            delay="long",
            rules=rules,
        )
    )
# M3, cells 64-69: the bank answers the first focal charge with `processing` (as real payment
# APIs do); it settles or fails only after the episode. No rules block, so nothing hints at
# "processing is not paid"; the orchestrator can check the bank itself.
for fault, channel in [
    (f, ch) for ch in ("text", "struct", "code") for f in ("processing_settles", "processing_fails")
]:
    _cells.append(
        Cell(
            number=len(_cells) + 1,
            block="processing",
            fault=fault,
            channel=channel,
            rules=False,
        )
    )
CELLS: dict[int, Cell] = {c.number: c for c in _cells}


class UnsupportedCell(ValueError):
    pass


def find_cell(
    fault: str,
    channel: str,
    *,
    delay: str = "short",
    rules: bool = True,
    agent: str = "delegated",
    key: str = "subagent",
) -> Cell:
    wanted = (fault, channel, delay, rules, agent, key)
    for cell in CELLS.values():
        if (cell.fault, cell.channel, cell.delay, cell.rules, cell.agent, cell.key) == wanted:
            return cell
    raise UnsupportedCell(
        f"no cell with fault={fault!r}, channel={channel!r}, delay={delay!r}, "
        f"rules={rules}, agent={agent!r}, key={key!r}"
    )
