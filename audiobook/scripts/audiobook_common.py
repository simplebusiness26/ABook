#!/usr/bin/env python3
"""Shared helpers for the ABook audiobook pipeline."""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
AUDIOBOOK_DIR = SCRIPT_DIR.parent
REPO_ROOT = AUDIOBOOK_DIR.parent
DEFAULT_CONFIG = AUDIOBOOK_DIR / "config" / "audiobook.toml"
DEFAULT_WORK_DIR = AUDIOBOOK_DIR / "work"
DEFAULT_EXPORT_DIR = AUDIOBOOK_DIR / "exports"


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    if config.get("schema_version") != 1:
        raise ValueError(f"Unsupported config schema in {config_path}")
    return config


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path | str, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def all_segments(manifest: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from manifest.get("front_matter", [])
    for chapter in manifest["chapters"]:
        yield from chapter["segments"]


def segment_audio_path(work_dir: Path, segment: dict[str, Any]) -> Path:
    return work_dir / "segments" / f"{segment['id']}-{segment['fingerprint'][:12]}.flac"


def safe_slug(value: str) -> str:
    value = value.lower().replace("’", "").replace("'", "")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "chapter"


def relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())

