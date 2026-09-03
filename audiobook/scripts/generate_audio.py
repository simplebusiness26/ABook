#!/usr/bin/env python3
"""Generate resumable FLAC narration segments with Kokoro."""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from audiobook_common import (
    DEFAULT_CONFIG,
    DEFAULT_WORK_DIR,
    all_segments,
    atomic_write_json,
    load_config,
    load_json,
    relative_to_repo,
    segment_audio_path,
)


def select_segments(
    manifest: dict[str, Any],
    config: dict[str, Any],
    *,
    sample: bool,
    chapter_number: int | None,
    limit: int | None,
) -> tuple[list[dict[str, Any]], str]:
    if sample:
        target_chapter = chapter_number or int(config["production"]["sample_chapter"])
        chapters = [c for c in manifest["chapters"] if c["number"] == target_chapter]
        if not chapters:
            raise ValueError(f"Sample chapter {target_chapter} does not exist")
        budget = int(config["production"]["sample_max_characters"])
        selected = list(manifest.get("front_matter", []))
        used = 0
        for segment in chapters[0]["segments"]:
            if used and used + len(segment["text"]) > budget:
                break
            selected.append(segment)
            used += len(segment["text"])
        label = f"approval sample from chapter {target_chapter}"
    elif chapter_number is not None:
        chapters = [c for c in manifest["chapters"] if c["number"] == chapter_number]
        if not chapters:
            raise ValueError(f"Chapter {chapter_number} does not exist")
        selected = list(chapters[0]["segments"])
        label = f"chapter {chapter_number}"
    else:
        selected = list(all_segments(manifest))
        label = "complete audiobook"

    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least 1")
        selected = selected[:limit]
        label += f" (first {limit} segments)"
    return selected, label


def valid_existing_audio(path: Path, expected_rate: int) -> bool:
    if not path.is_file() or path.stat().st_size < 256:
        return False
    try:
        import soundfile as sf

        info = sf.info(path)
        return info.frames > 0 and info.samplerate == expected_rate and info.channels == 1
    except Exception:
        return False


def write_selection(
    destination: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    selected: list[dict[str, Any]],
    work_dir: Path,
    label: str,
) -> dict[str, Any]:
    selection = {
        "schema_version": 1,
        "label": label,
        "book": manifest["book"],
        "manifest": relative_to_repo(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "complete_manifest": len(selected) == sum(1 for _ in all_segments(manifest)),
        "segments": [
            {
                "id": segment["id"],
                "chapter": segment["chapter"],
                "kind": segment["kind"],
                "fingerprint": segment["fingerprint"],
                "audio_file": segment_audio_path(work_dir, segment)
                .relative_to(work_dir)
                .as_posix(),
            }
            for segment in selected
        ],
    }
    atomic_write_json(destination, selection)
    return selection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_WORK_DIR / "manifest.json")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sample", action="store_true", help="Generate the short approval sample")
    mode.add_argument("--chapter", type=int, help="Generate one logical dated chapter")
    parser.add_argument("--limit", type=int, help="Limit generation for a smoke test")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu", "mps"), default="auto")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--selection-file", type=Path, default=DEFAULT_WORK_DIR / "selection.json"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest)
    config = load_config(args.config)
    selected, label = select_segments(
        manifest,
        config,
        sample=args.sample,
        chapter_number=args.chapter,
        limit=args.limit,
    )
    selection = write_selection(
        args.selection_file, args.manifest, manifest, selected, args.work_dir, label
    )
    character_count = sum(len(segment["text"]) for segment in selected)
    print(
        f"Selected {len(selected)} segments for {label}; approximately "
        f"{character_count / 1000:.1f} minutes of finished narration."
    )
    if args.dry_run:
        print(f"Dry run complete. Selection: {relative_to_repo(args.selection_file)}")
        return 0

    try:
        import numpy as np
        import soundfile as sf
        import torch
        from kokoro import KPipeline
    except ImportError as exc:
        print(
            "Audio dependencies are missing. Install audiobook/requirements-kaggle.txt "
            "and espeak-ng, or run the supplied Kaggle notebook.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 2

    expected_version = manifest["production"]["engine_version"]
    try:
        installed_version = version("kokoro")
    except PackageNotFoundError:
        installed_version = "missing"
    if installed_version != expected_version:
        print(
            f"Expected Kokoro {expected_version}, but found {installed_version}. "
            "Install the pinned audiobook requirements before generating.",
            file=sys.stderr,
        )
        return 2

    requested_device = None if args.device == "auto" else args.device
    if args.device == "cuda" and not torch.cuda.is_available():
        print("CUDA was requested but is unavailable. Enable a Kaggle GPU accelerator.", file=sys.stderr)
        return 2

    print(f"Loading {manifest['production']['model']} on {args.device}...")
    pipeline = KPipeline(
        lang_code=manifest["production"]["language_code"],
        repo_id=manifest["production"]["model"],
        device=requested_device,
    )

    args.work_dir.mkdir(parents=True, exist_ok=True)
    (args.work_dir / "segments").mkdir(parents=True, exist_ok=True)
    state_path = args.work_dir / "generation-state.json"
    state = load_json(state_path) if state_path.exists() else {
        "schema_version": 1,
        "book": manifest["book"]["title"],
        "segments": {},
    }
    internal_silence = np.zeros(round(int(manifest["production"]["sample_rate"]) * 0.06), dtype=np.float32)

    generated = 0
    skipped = 0
    for position, segment in enumerate(selected, start=1):
        output = segment_audio_path(args.work_dir, segment)
        if not args.overwrite and valid_existing_audio(output, int(segment["sample_rate"])):
            skipped += 1
            print(f"[{position}/{len(selected)}] resume {segment['id']}")
            continue

        print(
            f"[{position}/{len(selected)}] generate {segment['id']} "
            f"({segment['voice']} at {segment['speed']:.2f}x)"
        )
        try:
            audio_parts: list[Any] = []
            for result in pipeline(
                segment["text"],
                voice=segment["voice"],
                speed=float(segment["speed"]),
                split_pattern=None,
            ):
                if result.audio is None:
                    continue
                audio = result.audio.detach().cpu().numpy().astype(np.float32, copy=False)
                if audio_parts:
                    audio_parts.append(internal_silence)
                audio_parts.append(audio)
            if not audio_parts:
                raise RuntimeError("the TTS engine returned no audio")
            pause = np.zeros(
                round(int(segment["sample_rate"]) * int(segment["pause_after_ms"]) / 1000),
                dtype=np.float32,
            )
            audio_parts.append(pause)
            combined = np.concatenate(audio_parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            sf.write(output, combined, int(segment["sample_rate"]), format="FLAC", subtype="PCM_16")
            duration = len(combined) / int(segment["sample_rate"])
            state["segments"][segment["id"]] = {
                "fingerprint": segment["fingerprint"],
                "audio_file": output.relative_to(args.work_dir).as_posix(),
                "duration_seconds": round(duration, 3),
                "voice": segment["voice"],
                "status": "complete",
            }
            state["updated_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_write_json(state_path, state)
            generated += 1
        except Exception as exc:
            state["segments"][segment["id"]] = {
                "fingerprint": segment["fingerprint"],
                "audio_file": output.relative_to(args.work_dir).as_posix(),
                "status": "failed",
                "error": str(exc),
            }
            state["updated_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_write_json(state_path, state)
            raise

    selection["generated_now"] = generated
    selection["resumed_existing"] = skipped
    atomic_write_json(args.selection_file, selection)
    print(
        f"Generation complete: {generated} created, {skipped} resumed. "
        f"State: {relative_to_repo(state_path)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
