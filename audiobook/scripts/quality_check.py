#!/usr/bin/env python3
"""Perform structural checks on generated audiobook segment audio."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

from audiobook_common import (
    DEFAULT_WORK_DIR,
    all_segments,
    atomic_write_json,
    load_json,
    relative_to_repo,
    segment_audio_path,
)


def selected_items(
    manifest: dict[str, Any], work_dir: Path, selection_path: Path | None
) -> list[tuple[dict[str, Any], Path]]:
    lookup = {segment["id"]: segment for segment in all_segments(manifest)}
    if selection_path is None:
        return [(segment, segment_audio_path(work_dir, segment)) for segment in lookup.values()]
    selection = load_json(selection_path)
    result = []
    for item in selection.get("segments", []):
        segment = lookup.get(item["id"])
        if segment is None:
            raise ValueError(f"Unknown selected segment {item['id']}")
        result.append((segment, work_dir / item["audio_file"]))
    return result


def inspect_audio(path: Path, expected_rate: int) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    info = sf.info(path)
    peak = 0.0
    square_sum = 0.0
    sample_count = 0
    for block in sf.blocks(path, blocksize=65536, dtype="float32", always_2d=True):
        if block.size:
            peak = max(peak, float(np.max(np.abs(block))))
            square_sum += float(np.sum(np.square(block, dtype=np.float64)))
            sample_count += int(block.size)
    rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
    issues = []
    if info.channels != 1:
        issues.append(f"expected mono; found {info.channels} channels")
    if info.samplerate != expected_rate:
        issues.append(f"expected {expected_rate} Hz; found {info.samplerate} Hz")
    if info.frames <= 0:
        issues.append("contains no audio frames")
    if peak >= 0.999:
        issues.append("possible clipping detected")
    if peak == 0.0:
        issues.append("segment is completely silent")
    return {
        "file": relative_to_repo(path),
        "duration_seconds": round(info.duration, 3),
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_WORK_DIR / "manifest.json")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--selection", type=Path, help="Check only a generated selection")
    parser.add_argument("--report", type=Path, default=DEFAULT_WORK_DIR / "quality-report.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import numpy  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError as exc:
        print(f"Audio QC dependencies are missing: {exc}", file=sys.stderr)
        return 2

    manifest = load_json(args.manifest)
    items = selected_items(manifest, args.work_dir, args.selection)
    missing = []
    reports = []
    for segment, path in items:
        if not path.is_file():
            missing.append(relative_to_repo(path))
            continue
        report = inspect_audio(path, int(segment["sample_rate"]))
        report["segment_id"] = segment["id"]
        reports.append(report)

    issue_count = sum(len(report["issues"]) for report in reports)
    result = {
        "schema_version": 1,
        "checked_segments": len(reports),
        "expected_segments": len(items),
        "missing_segments": missing,
        "issue_count": issue_count,
        "total_duration_seconds": round(sum(r["duration_seconds"] for r in reports), 3),
        "segments": reports,
        "human_review_required": [
            "pronunciation",
            "voice consistency",
            "pacing and pauses",
            "misread names or slang",
            "unwanted digital artefacts",
        ],
    }
    atomic_write_json(args.report, result)
    print(
        f"Checked {len(reports)}/{len(items)} segments; "
        f"{len(missing)} missing and {issue_count} structural audio issues."
    )
    print(f"Report: {relative_to_repo(args.report)}")
    return 1 if missing or issue_count else 0


if __name__ == "__main__":
    raise SystemExit(main())

