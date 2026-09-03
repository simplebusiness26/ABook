#!/usr/bin/env python3
"""Assemble generated FLAC segments into an approval sample, MP3 chapters and M4B."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from audiobook_common import (
    DEFAULT_CONFIG,
    DEFAULT_EXPORT_DIR,
    DEFAULT_WORK_DIR,
    all_segments,
    atomic_write_json,
    load_config,
    load_json,
    relative_to_repo,
    safe_slug,
    segment_audio_path,
)


def require_program(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(f"{name} is required but was not found")
    return resolved


def ffconcat_escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def write_concat_list(path: Path, audio_paths: Iterable[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"file '{ffconcat_escape(item)}'" for item in audio_paths]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def duration_seconds(path: Path, ffprobe: str) -> float:
    completed = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def ffmetadata_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("\n", " ")
    )


def segment_lookup(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {segment["id"]: segment for segment in all_segments(manifest)}


def sample_items(
    manifest: dict[str, Any], selection: dict[str, Any], work_dir: Path
) -> list[tuple[dict[str, Any], Path]]:
    lookup = segment_lookup(manifest)
    items: list[tuple[dict[str, Any], Path]] = []
    for selected in selection.get("segments", []):
        segment = lookup.get(selected["id"])
        if not segment:
            raise ValueError(f"Selection contains unknown segment {selected['id']}")
        if segment["fingerprint"] != selected["fingerprint"]:
            raise ValueError(f"Selection is stale for segment {selected['id']}")
        items.append((segment, work_dir / selected["audio_file"]))
    return items


def full_items(manifest: dict[str, Any], work_dir: Path) -> list[tuple[dict[str, Any], Path]]:
    return [(segment, segment_audio_path(work_dir, segment)) for segment in all_segments(manifest)]


def ensure_audio_exists(items: list[tuple[dict[str, Any], Path]], dry_run: bool) -> bool:
    missing = [path for _, path in items if not path.is_file() or path.stat().st_size < 256]
    if missing:
        print(f"Missing {len(missing)} of {len(items)} generated audio segments.")
        for path in missing[:10]:
            print(f"- {relative_to_repo(path)}")
        if len(missing) > 10:
            print(f"- ...and {len(missing) - 10} more")
        return dry_run
    return True


def metadata_args(book: dict[str, Any], title: str) -> list[str]:
    values = ["-metadata", f"title={title}", "-metadata", f"album={book['title']}"]
    if book.get("author"):
        values += ["-metadata", f"artist={book['author']}", "-metadata", f"album_artist={book['author']}"]
    return values


def audio_filter(config: dict[str, Any]) -> str:
    mastering = config["mastering"]
    return (
        f"loudnorm=I={mastering['target_lufs']}:"
        f"TP={mastering['true_peak_db']}:LRA={mastering['loudness_range']}"
    )


def build_sample(
    items: list[tuple[dict[str, Any], Path]],
    manifest: dict[str, Any],
    config: dict[str, Any],
    work_dir: Path,
    export_dir: Path,
    ffmpeg: str,
    overwrite: bool,
) -> Path:
    mode = manifest["production"]["voice_mode"]
    slug = manifest["book"]["output_slug"]
    output = export_dir / "approval-samples" / f"{slug}-{mode}-approval-sample.mp3"
    if output.exists() and not overwrite:
        print(f"Approval sample already exists: {relative_to_repo(output)}")
        return output

    concat_file = work_dir / "ffmpeg" / "sample-concat.txt"
    write_concat_list(concat_file, [path for _, path in items])
    output.parent.mkdir(parents=True, exist_ok=True)
    mastering = config["mastering"]
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-af", audio_filter(config),
        "-ar", str(mastering["sample_rate"]),
        "-ac", str(mastering["channels"]),
        "-c:a", "libmp3lame", "-b:a", mastering["mp3_bitrate"],
        *metadata_args(manifest["book"], f"{manifest['book']['title']} - approval sample"),
        str(output),
    ]
    run(command)
    return output


def build_chapter_files(
    chapter_number: int,
    chapter_title: str,
    items: list[tuple[dict[str, Any], Path]],
    manifest: dict[str, Any],
    config: dict[str, Any],
    work_dir: Path,
    export_dir: Path,
    ffmpeg: str,
    overwrite: bool,
) -> tuple[Path, Path]:
    slug = safe_slug(chapter_title)
    stem = f"{chapter_number:03d}-{slug}"
    m4a = work_dir / "mastered" / f"{stem}.m4a"
    mp3 = export_dir / "chapters" / f"{stem}.mp3"
    if m4a.exists() and mp3.exists() and not overwrite:
        print(f"Resume mastered chapter {chapter_number:03d}")
        return m4a, mp3

    concat_file = work_dir / "ffmpeg" / f"{stem}-concat.txt"
    write_concat_list(concat_file, [path for _, path in items])
    m4a.parent.mkdir(parents=True, exist_ok=True)
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mastering = config["mastering"]
    filter_graph = f"[0:a]{audio_filter(config)},asplit=2[m4b][mp3]"
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-filter_complex", filter_graph,
        "-map", "[m4b]",
        "-ar", str(mastering["sample_rate"]),
        "-ac", str(mastering["channels"]),
        "-c:a", "aac", "-b:a", mastering["m4b_bitrate"],
        *metadata_args(manifest["book"], chapter_title),
        str(m4a),
        "-map", "[mp3]",
        "-ar", str(mastering["sample_rate"]),
        "-ac", str(mastering["channels"]),
        "-c:a", "libmp3lame", "-b:a", mastering["mp3_bitrate"],
        *metadata_args(manifest["book"], chapter_title),
        str(mp3),
    ]
    run(command)
    return m4a, mp3


def build_m4b(
    chapter_outputs: list[tuple[str, Path, Path]],
    manifest: dict[str, Any],
    work_dir: Path,
    export_dir: Path,
    ffmpeg: str,
    ffprobe: str,
    overwrite: bool,
) -> tuple[Path, list[dict[str, Any]]]:
    slug = manifest["book"]["output_slug"]
    output = export_dir / f"{slug}.m4b"
    durations = [duration_seconds(m4a, ffprobe) for _, m4a, _ in chapter_outputs]
    chapter_info: list[dict[str, Any]] = []
    cursor_ms = 0
    metadata_lines = [";FFMETADATA1", f"title={ffmetadata_escape(manifest['book']['title'])}"]
    if manifest["book"].get("author"):
        metadata_lines.append(f"artist={ffmetadata_escape(manifest['book']['author'])}")
    for (title, m4a, mp3), duration in zip(chapter_outputs, durations):
        end_ms = cursor_ms + max(1, round(duration * 1000))
        metadata_lines.extend(
            [
                "[CHAPTER]", "TIMEBASE=1/1000", f"START={cursor_ms}", f"END={end_ms}",
                f"title={ffmetadata_escape(title)}",
            ]
        )
        chapter_info.append(
            {
                "title": title,
                "start_ms": cursor_ms,
                "end_ms": end_ms,
                "duration_seconds": round(duration, 3),
                "mp3": relative_to_repo(mp3),
                "m4a_working_file": relative_to_repo(m4a),
            }
        )
        cursor_ms = end_ms

    metadata_file = work_dir / "ffmpeg" / "book-metadata.txt"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text("\n".join(metadata_lines) + "\n", encoding="utf-8")
    concat_file = work_dir / "ffmpeg" / "book-concat.txt"
    write_concat_list(concat_file, [m4a for _, m4a, _ in chapter_outputs])
    output.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not output.exists():
        run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-i", str(metadata_file),
                "-map", "0:a", "-map_metadata", "1", "-c", "copy",
                "-movflags", "+faststart", str(output),
            ]
        )
    return output, chapter_info


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_WORK_DIR / "manifest.json")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR)
    parser.add_argument("--selection", type=Path, default=DEFAULT_WORK_DIR / "selection.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sample", action="store_true")
    mode.add_argument("--full", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest)
    config = load_config(args.config)
    if args.sample:
        selection = load_json(args.selection)
        items = sample_items(manifest, selection, args.work_dir)
    else:
        items = full_items(manifest, args.work_dir)

    if not ensure_audio_exists(items, args.dry_run):
        return 2
    if args.dry_run:
        print(f"Mastering dry run complete for {len(items)} segments.")
        return 0

    try:
        ffmpeg = require_program("ffmpeg")
        ffprobe = require_program("ffprobe")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.sample:
        output = build_sample(
            items, manifest, config, args.work_dir, args.export_dir, ffmpeg, args.overwrite
        )
        duration = duration_seconds(output, ffprobe)
        report = {
            "schema_version": 1,
            "type": "approval_sample",
            "voice_mode": manifest["production"]["voice_mode"],
            "file": relative_to_repo(output),
            "duration_seconds": round(duration, 3),
            "source_sha256": manifest["source"]["sha256"],
        }
        atomic_write_json(args.export_dir / "approval-samples" / "sample-report.json", report)
        print(f"Approval sample ready: {relative_to_repo(output)} ({duration / 60:.1f} minutes)")
        return 0

    grouped: dict[int, list[tuple[dict[str, Any], Path]]] = {}
    for item in items:
        grouped.setdefault(int(item[0]["chapter"]), []).append(item)
    chapter_titles = {0: "Opening"}
    chapter_titles.update({c["number"]: c["title"] for c in manifest["chapters"]})
    outputs: list[tuple[str, Path, Path]] = []
    for number in sorted(grouped):
        title = chapter_titles[number]
        print(f"Mastering {number:03d}: {title}")
        m4a, mp3 = build_chapter_files(
            number, title, grouped[number], manifest, config, args.work_dir,
            args.export_dir, ffmpeg, args.overwrite,
        )
        outputs.append((title, m4a, mp3))

    m4b, chapter_info = build_m4b(
        outputs, manifest, args.work_dir, args.export_dir, ffmpeg, ffprobe, args.overwrite
    )
    report = {
        "schema_version": 1,
        "type": "complete_audiobook",
        "voice_mode": manifest["production"]["voice_mode"],
        "file": relative_to_repo(m4b),
        "sha256": hashlib.sha256(m4b.read_bytes()).hexdigest(),
        "duration_seconds": round(sum(c["duration_seconds"] for c in chapter_info), 3),
        "source_sha256": manifest["source"]["sha256"],
        "chapters": chapter_info,
    }
    atomic_write_json(args.export_dir / "audiobook-report.json", report)
    print(
        f"Complete M4B ready: {relative_to_repo(m4b)} "
        f"({report['duration_seconds'] / 3600:.2f} hours)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

