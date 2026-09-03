#!/usr/bin/env python3
"""Pack or restore hash-addressed audiobook checkpoints between Kaggle sessions."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

from audiobook_common import DEFAULT_EXPORT_DIR, DEFAULT_WORK_DIR, relative_to_repo


CHECKPOINT_NAME = "abook-audiobook-checkpoint.zip"


def safe_extract(archive: Path, destination: Path) -> int:
    destination = destination.resolve()
    count = 0
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = (destination / member.filename).resolve()
            if destination != target and destination not in target.parents:
                raise ValueError(f"Unsafe path in checkpoint: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as incoming, target.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            count += 1
    return count


def pack(work_dir: Path, output: Path) -> int:
    candidates = [
        work_dir / "manifest.json",
        work_dir / "narration-script.txt",
        work_dir / "selection.json",
        work_dir / "generation-state.json",
        work_dir / "quality-report.json",
    ]
    candidates.extend(sorted((work_dir / "segments").glob("*.flac")))
    files = [path for path in candidates if path.is_file()]
    if not files:
        raise FileNotFoundError(f"No audiobook work exists in {work_dir}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, path.relative_to(work_dir).as_posix())
    print(f"Packed {len(files)} files: {relative_to_repo(output)}")
    return 0


def newest_checkpoint(search_root: Path) -> Path | None:
    if not search_root.exists():
        return None
    matches = list(search_root.rglob(CHECKPOINT_NAME))
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pack_parser = subparsers.add_parser("pack")
    pack_parser.add_argument("--output", type=Path, default=DEFAULT_EXPORT_DIR / CHECKPOINT_NAME)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("archive", type=Path)
    auto_parser = subparsers.add_parser("auto-restore")
    auto_parser.add_argument("--search-root", type=Path, default=Path("/kaggle/input"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "pack":
        return pack(args.work_dir, args.output)
    if args.command == "auto-restore":
        archive = newest_checkpoint(args.search_root)
        if archive is None:
            print(f"No {CHECKPOINT_NAME} found under {args.search_root}; starting clean.")
            return 0
    else:
        archive = args.archive
    count = safe_extract(archive, args.work_dir)
    print(f"Restored {count} files from {archive} into {relative_to_repo(args.work_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

