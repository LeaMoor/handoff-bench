"""Журнал эффектов: строка на каждое реальное списание. Пишет только банк, читает только грейдер."""

import json
from pathlib import Path

from handoff_bench.billing.models import EffectRow


def dump_row(row: EffectRow) -> str:
    """Одна строка JSONL. Ключи отсортированы, разделители фиксированы — вывод воспроизводим."""
    return json.dumps(row.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class EffectJournal:
    """Держит строки в памяти и, если задан путь, дописывает их в JSONL."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.rows: list[EffectRow] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def write(self, row: EffectRow) -> None:
        self.rows.append(row)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(dump_row(row) + "\n")

    @staticmethod
    def read(path: Path) -> list[EffectRow]:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [EffectRow.model_validate_json(line) for line in lines if line]
