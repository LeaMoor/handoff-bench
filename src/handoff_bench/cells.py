from typing import Literal

from pydantic import BaseModel, ConfigDict

Block = Literal["control", "calibration", "main"]
Fault = Literal["none", "pre", "post", "timeout_lost", "timeout_committed"]
Channel = Literal["text", "struct", "code", "none"]  # none: a single agent, no delegate
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
) -> Cell:
    key = (fault, channel, delay, rules, agent)
    for cell in CELLS.values():
        if (cell.fault, cell.channel, cell.delay, cell.rules, cell.agent) == key:
            return cell
    raise UnsupportedCell(
        f"no cell with fault={fault!r}, channel={channel!r}, delay={delay!r}, "
        f"rules={rules}, agent={agent!r}"
    )
