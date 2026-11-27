"""Do not change the format: pre / post equivalence relies on identical bytes."""

import json
from pathlib import Path

from pydantic import BaseModel


def dumps(row: BaseModel) -> str:
    return json.dumps(
        row.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


class JsonlWriter[RowT: BaseModel]:
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
