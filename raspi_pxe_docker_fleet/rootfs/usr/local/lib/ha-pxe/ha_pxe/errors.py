"""Custom exceptions used across the ha_pxe package."""

from __future__ import annotations


class HaPxeError(Exception):
    """Base exception for PXE provisioning failures."""


class CommandError(HaPxeError):
    """Raised when an external command exits with a non-zero status."""

    def __init__(self, command: list[str], returncode: int, stderr: str = "", stdout: str = "") -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr.strip()
        self.stdout = stdout.strip()
        detail = f"Command failed with status {returncode}: {' '.join(command)}"
        if self.stderr:
            detail = f"{detail}: {self.stderr}"
        super().__init__(detail)


class SpecError(HaPxeError):
    """Raised for invalid container specifications."""

