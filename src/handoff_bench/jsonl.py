"""Единый формат JSONL для всех журналов: ключи отсортированы, разделители фиксированы.

Нужен, чтобы одинаковые данные всегда давали одинаковые байты (тесты эквивалентности pre/post).
"""

import json
from pathlib import Path

from pydantic import BaseModel


def dumps(row: BaseModel) -> str:
    return json.dumps(
        row.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


class JsonlWriter[RowT: BaseModel]:
    """Держит строки в памяти и, если задан путь, дописывает их в файл.

    Файл создаётся новым. Если он уже есть — ошибка: журналы прошлого прогона молча
    не затираем (overwrite=True — затереть сознательно).
    """

    def __init__(self, path: Path | None = None, *, overwrite: bool = False) -> None:
        self.path = path
        self.rows: list[RowT] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w" if overwrite else "x", encoding="utf-8"):
                pass

    def write(self, row: RowT) -> None:
        self.rows.append(row)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(dumps(row) + "\n")


def read_rows[RowT: BaseModel](path: Path, model: type[RowT]) -> list[RowT]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [model.model_validate_json(line) for line in lines if line]
