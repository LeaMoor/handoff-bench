from typing import Literal

from pydantic import BaseModel, ConfigDict

Block = Literal["control", "calibration", "main"]
Fault = Literal["none", "pre", "post", "timeout_lost", "timeout_committed"]
Channel = Literal["text", "struct", "code"]


class Cell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int
    block: Block
    fault: Fault
    channel: Channel


CELLS: dict[int, Cell] = {
    c.number: c
    for c in (
        Cell(number=1, block="control", fault="none", channel="text"),
        Cell(number=2, block="calibration", fault="pre", channel="text"),
        Cell(number=3, block="calibration", fault="post", channel="text"),
        Cell(number=4, block="calibration", fault="pre", channel="code"),
        Cell(number=5, block="calibration", fault="post", channel="code"),
        Cell(number=6, block="main", fault="timeout_lost", channel="text"),
        Cell(number=7, block="main", fault="timeout_committed", channel="text"),
        Cell(number=8, block="main", fault="timeout_lost", channel="struct"),
        Cell(number=9, block="main", fault="timeout_committed", channel="struct"),
        Cell(number=10, block="main", fault="timeout_lost", channel="code"),
        Cell(number=11, block="main", fault="timeout_committed", channel="code"),
    )
}


class UnsupportedCell(ValueError):
    pass


def find_cell(fault: str, channel: str) -> Cell:
    for cell in CELLS.values():
        if (cell.fault, cell.channel) == (fault, channel):
            return cell
    raise UnsupportedCell(f"no cell with fault={fault!r} and channel={channel!r}")
