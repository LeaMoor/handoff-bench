"""Журнал эффектов: строка на каждое реальное списание. Пишет только банк, читает только грейдер."""

from pathlib import Path

from handoff_bench.billing.models import EffectRow
from handoff_bench.jsonl import JsonlWriter, dumps, read_rows


def dump_row(row: EffectRow) -> str:
    return dumps(row)


class EffectJournal(JsonlWriter[EffectRow]):
    @staticmethod
    def read(path: Path) -> list[EffectRow]:
        return read_rows(path, EffectRow)
