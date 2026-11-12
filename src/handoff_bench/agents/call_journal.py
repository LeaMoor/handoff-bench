"""Журнал вызовов: строка на каждый вызов инструмента любым агентом.

В отличие от журнала эффектов, сюда пишется то, что видел агент: что он вызвал и что ему
вернулось. Из этого журнала (и только из него) строится канал «выписка». Пишет — шлюз вызовов.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.tools import Outcome  # ok | error | timeout | dropped | infra_error
from handoff_bench.jsonl import JsonlWriter, read_rows


class CallRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    episode_id: str
    # Порядковый номер вызова в эпизоде, сквозной для всех агентов (1, 2, 3…), по времени начала.
    seq: int
    actor: str
    tool: str
    args: dict[str, Any]
    outcome: Outcome
    # Данные успешного ответа (как их получил агент), иначе None.
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None


class CallJournal(JsonlWriter[CallRow]):
    @staticmethod
    def read(path: Path) -> list[CallRow]:
        return read_rows(path, CallRow)

    def by_actor(self, actor: str) -> list[CallRow]:
        return [r for r in self.rows if r.actor == actor]
