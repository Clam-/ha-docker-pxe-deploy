"""Subprocess helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import IO

from .errors import CommandError


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    stdout: IO[str] | int | None = None,
    stderr: IO[str] | int | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=capture_output,
        cwd=str(cwd) if cwd else None,
        env=env,
        input=input_text,
        stdout=stdout,
        stderr=stderr,
    )
    if check and completed.returncode != 0:
        raise CommandError(command, completed.returncode, completed.stderr or "", completed.stdout or "")
    return completed


def capture(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = run(command, capture_output=True, cwd=cwd, env=env)
    return completed.stdout.strip()


def capture_optional(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = run(command, check=False, capture_output=True, cwd=cwd, env=env)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def spawn(command: list[str], *, cwd: Path | None = None) -> subprocess.Popen[str]:
    return subprocess.Popen(command, cwd=str(cwd) if cwd else None, text=True)
