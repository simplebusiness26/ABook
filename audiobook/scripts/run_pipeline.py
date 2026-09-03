#!/usr/bin/env python3
"""One-command orchestration for ABook's sample or full audiobook build."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from audiobook_common import AUDIOBOOK_DIR, DEFAULT_CONFIG, DEFAULT_EXPORT_DIR, DEFAULT_WORK_DIR


SCRIPTS = AUDIOBOOK_DIR / "scripts"


def run(*arguments: str | Path) -> None:
    command = [sys.executable, *(str(item) for item in arguments)]
    print("\n$ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sample", "full"), default="sample")
    parser.add_argument("--voice-mode", choices=("narrator", "pov"), default="narrator")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu", "mps"), default="auto")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR)
    parser.add_argument("--restore", type=Path, help="Restore a checkpoint ZIP before building")
    parser.add_argument("--auto-restore", action="store_true", help="Search /kaggle/input for a checkpoint")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = args.work_dir / "manifest.json"
    narration_script = args.work_dir / "narration-script.txt"
    selection = args.work_dir / "selection.json"

    if args.restore:
        run(SCRIPTS / "checkpoint.py", "--work-dir", args.work_dir, "restore", args.restore)
    elif args.auto_restore:
        run(SCRIPTS / "checkpoint.py", "--work-dir", args.work_dir, "auto-restore")

    run(
        SCRIPTS / "prepare_manuscript.py",
        "--config", args.config,
        "--output", manifest,
        "--script-output", narration_script,
        "--voice-mode", args.voice_mode,
    )
    run(SCRIPTS / "validate_manifest.py", "--config", args.config, "--manifest", manifest)

    generate: list[str | Path] = [
        SCRIPTS / "generate_audio.py",
        "--config", args.config,
        "--manifest", manifest,
        "--work-dir", args.work_dir,
        "--selection-file", selection,
        "--device", args.device,
    ]
    if args.mode == "sample":
        generate.append("--sample")
    if args.overwrite:
        generate.append("--overwrite")
    if args.dry_run:
        generate.append("--dry-run")
    run(*generate)

    if not args.dry_run:
        qc: list[str | Path] = [
            SCRIPTS / "quality_check.py",
            "--manifest", manifest,
            "--work-dir", args.work_dir,
            "--report", args.work_dir / "quality-report.json",
        ]
        if args.mode == "sample":
            qc.extend(["--selection", selection])
        run(*qc)

    master: list[str | Path] = [
        SCRIPTS / "master_audio.py",
        "--config", args.config,
        "--manifest", manifest,
        "--work-dir", args.work_dir,
        "--export-dir", args.export_dir,
        "--selection", selection,
        "--sample" if args.mode == "sample" else "--full",
    ]
    if args.overwrite:
        master.append("--overwrite")
    if args.dry_run:
        master.append("--dry-run")
    run(*master)

    if not args.dry_run:
        run(
            SCRIPTS / "checkpoint.py",
            "--work-dir", args.work_dir,
            "pack",
            "--output", args.export_dir / "abook-audiobook-checkpoint.zip",
        )

    print(f"\nABook audiobook {args.mode} pipeline complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
