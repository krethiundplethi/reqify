from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def init_repo(repo: Path) -> None:
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "reqify@localhost")
    run_git(repo, "config", "user.name", "Reqify")


def commit_repo(repo: Path, message: str) -> bool:
    run_git(repo, "add", ".")
    diff = run_git(repo, "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return False
    run_git(repo, "commit", "-m", message)
    return True
