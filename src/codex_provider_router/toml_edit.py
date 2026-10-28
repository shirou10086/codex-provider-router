from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib
from typing import Any


TABLE_HEADER_RE = re.compile(r"^\s*\[([^\]]+)]\s*(?:#.*)?$")


def parse_toml(source: str, label: str) -> dict[str, Any]:
    try:
        return tomllib.loads(source)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid TOML in {label}: {error}") from error


def toml_value(value: Any) -> str:
    if value is None:
        raise ValueError("None cannot be serialized as TOML")
    return json.dumps(value, ensure_ascii=True)


def update_table(source: str, table: str | None, updates: dict[str, Any]) -> str:
    lines = source.splitlines(keepends=True)
    if table is None:
        start = 0
        end = next(
            (index for index, line in enumerate(lines) if TABLE_HEADER_RE.match(line)),
            len(lines),
        )
    else:
        index = next(
            (
                index
                for index, line in enumerate(lines)
                if (match := TABLE_HEADER_RE.match(line)) and match.group(1) == table
            ),
            None,
        )
        if index is None:
            source = source.rstrip() + f"\n\n[{table}]\n"
            lines = source.splitlines(keepends=True)
            start = end = len(lines)
        else:
            start = index + 1
            end = next(
                (
                    index
                    for index in range(start, len(lines))
                    if TABLE_HEADER_RE.match(lines[index])
                ),
                len(lines),
            )
    pattern = re.compile(
        r"^\s*(" + "|".join(re.escape(key) for key in updates) + r")\s*="
    )
    body = [line for line in lines[start:end] if not pattern.match(line)]
    if body and not body[-1].endswith("\n"):
        body[-1] += "\n"
    body.extend(
        f"{key} = {toml_value(value)}\n"
        for key, value in updates.items()
        if value is not None
    )
    return "".join(lines[:start] + body + lines[end:])


def remove_table_tree(source: str, prefix: str) -> str:
    output: list[str] = []
    skipping = False
    for line in source.splitlines(keepends=True):
        if match := TABLE_HEADER_RE.match(line):
            name = match.group(1)
            skipping = name == prefix or name.startswith(f"{prefix}.")
        if not skipping:
            output.append(line)
    return "".join(output).rstrip() + "\n"


def ensure_private(path: Path, executable: bool = False) -> None:
    if not path.exists():
        raise ValueError(f"required file is missing: {path}")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError(f"permissions are too broad on {path}: {oct(mode)}")
    if executable and not mode & 0o100:
        raise ValueError(f"file is not executable: {path}")


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup(path: Path, directory: Path) -> Path | None:
    if not path.exists():
        return None
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    target = directory / f"{path.name}.{dt.datetime.now():%Y%m%d-%H%M%S-%f}"
    shutil.copy2(path, target)
    target.chmod(0o600)
    return target


def safe_write_config(path: Path, source: str, backup_dir: Path) -> Path | None:
    parse_toml(source, str(path))
    backup_path = backup(path, backup_dir)
    atomic_write(path, source)
    return backup_path
