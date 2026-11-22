"""The fake `claude` (tests/harness/fake_claude.py) as a binary for HarnessSettings."""

import sys
from pathlib import Path

FAKE = Path(__file__).parents[1] / "harness" / "fake_claude.py"
FINISH = ["finish", {"status": "done", "done": [], "uncertain": [], "message": "Paid."}]


def fake_binary(tmp_path: Path) -> str:
    path = tmp_path / "claude"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    path.chmod(0o755)
    return str(path)
