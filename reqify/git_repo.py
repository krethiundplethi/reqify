from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    print_git_debug(repo, command, result.returncode, result.stdout, result.stderr)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
    return result


def run_git_bytes(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    print_git_debug(
        repo,
        command,
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
    return result


def git_debug_enabled() -> bool:
    return os.environ.get("REQIFY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def print_git_debug(repo: Path, command: list[str], returncode: int, stdout: str, stderr: str) -> None:
    if not git_debug_enabled():
        return
    print("\n=== Reqify git debug ===", file=sys.stderr)
    print(f"cwd: {repo}", file=sys.stderr)
    print(f"$ {shlex.join(command)}", file=sys.stderr)
    print(f"exit: {returncode}", file=sys.stderr)
    print("--- stdout ---", file=sys.stderr)
    print(stdout.rstrip() if stdout else "<empty>", file=sys.stderr)
    print("--- stderr ---", file=sys.stderr)
    print(stderr.rstrip() if stderr else "<empty>", file=sys.stderr)
    print("=== end Reqify git debug ===\n", file=sys.stderr)


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
