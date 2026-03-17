"""Filesystem helpers."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, data: str, mode: int | None = None) -> None:
    ensure_directory(path.parent)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(data)
        tmp_name = handle.name
    if mode is not None:
        os.chmod(tmp_name, mode)
    os.replace(tmp_name, path)


def clear_directory(path: Path) -> None:
    ensure_directory(path)
    for entry in path.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)


def replace_symlink(link_path: Path, target: str) -> None:
    ensure_directory(link_path.parent)
    if link_path.exists() or link_path.is_symlink():
        if link_path.is_dir() and not link_path.is_symlink():
            shutil.rmtree(link_path)
        else:
            link_path.unlink()
    link_path.symlink_to(target)


def copy_file(src: Path, dst: Path, mode: int | None = None) -> None:
    ensure_directory(dst.parent)
    shutil.copy2(src, dst)
    if mode is not None:
        os.chmod(dst, mode)


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))

