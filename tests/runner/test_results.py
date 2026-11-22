import subprocess
from pathlib import Path

from handoff_bench.runner.results import git_commit


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_only_tracked_changes_make_the_commit_dirty(tmp_path) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "code.py").write_text("x = 1\n")
    git(tmp_path, "add", "code.py")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    sha = git_commit(tmp_path)

    (tmp_path / "local.yaml").write_text("untracked\n")
    assert git_commit(tmp_path) == sha

    (tmp_path / "code.py").write_text("x = 2\n")
    assert git_commit(tmp_path) == f"{sha}-dirty"
