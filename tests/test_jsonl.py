import pytest

from handoff_bench.billing import EffectJournal


def test_existing_journal_is_not_silently_overwritten(tmp_path) -> None:
    path = tmp_path / "effects.jsonl"
    EffectJournal(path)
    with pytest.raises(FileExistsError):
        EffectJournal(path)
    EffectJournal(path, overwrite=True)  # сознательно — можно
