"""Канал «выписка» строится только отсюда, не из журнала эффектов."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.tools import Outcome
from handoff_bench.jsonl import JsonlWriter, read_rows


class CallRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    episode_id: str
    seq: int
    actor: str
    tool: str
    args: dict[str, Any]
    outcome: Outcome
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None


class CallJournal(JsonlWriter[CallRow]):
    @staticmethod
    def read(path: Path) -> list[CallRow]:
        return read_rows(path, CallRow)

    def by_actor(self, actor: str) -> list[CallRow]:
        return [r for r in self.rows if r.actor == actor]
