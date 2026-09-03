#!/usr/bin/env python3
"""Validate completeness, ordering, voices and hashes in an audiobook manifest."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from audiobook_common import DEFAULT_CONFIG, DEFAULT_WORK_DIR, REPO_ROOT, all_segments, load_config, load_json
from prepare_manuscript import fingerprint_segment, source_digest


BRITISH_VOICE = re.compile(r"^b[fm]_[a-z0-9_]+(?:,b[fm]_[a-z0-9_]+)*$")


def validate(manifest: dict, config: dict) -> list[str]:
    errors: list[str] = []
    rules = config["validation"]
    production = manifest.get("production", {})
    chapters = manifest.get("chapters", [])
    segments = list(all_segments(manifest)) if chapters else []

    if manifest.get("schema_version") != 1:
        errors.append("manifest schema_version must be 1")
    if len(manifest.get("source", {}).get("files", [])) != rules["expected_source_files"]:
        errors.append(
            f"expected {rules['expected_source_files']} source files; "
            f"found {len(manifest.get('source', {}).get('files', []))}"
        )
    if len(chapters) != rules["expected_chapters"]:
        errors.append(f"expected {rules['expected_chapters']} chapters; found {len(chapters)}")
    if chapters and chapters[0].get("title") != rules["first_chapter"]:
        errors.append("the first chapter is not the configured canonical opening")
    if chapters and chapters[-1].get("title") != rules["last_chapter"]:
        errors.append("the last chapter is not the configured canonical ending")

    expected_numbers = list(range(1, len(chapters) + 1))
    actual_numbers = [chapter.get("number") for chapter in chapters]
    if actual_numbers != expected_numbers:
        errors.append("chapter numbers are not consecutive and ordered")

    ids = [segment.get("id") for segment in segments]
    if len(ids) != len(set(ids)):
        errors.append("segment IDs are not unique")

    maximum = int(production.get("max_segment_characters", 0))
    for segment in segments:
        label = segment.get("id", "unknown")
        if not segment.get("text", "").strip():
            errors.append(f"{label} has no spoken text")
        if segment.get("kind") == "body" and len(segment.get("text", "")) > maximum:
            errors.append(f"{label} exceeds the configured body-segment character limit")
        if not BRITISH_VOICE.match(segment.get("voice", "")):
            errors.append(f"{label} does not use a configured British voice")
        if segment.get("fingerprint") != fingerprint_segment(segment):
            errors.append(f"{label} has a stale or invalid fingerprint")
        if "**" in segment.get("text", "") or "`" in segment.get("text", ""):
            errors.append(f"{label} still contains Markdown markup")

    spoken_words = int(manifest.get("stats", {}).get("spoken_words", 0))
    if spoken_words < rules["minimum_spoken_words"]:
        errors.append(
            f"spoken word count {spoken_words:,} is below the configured minimum "
            f"of {rules['minimum_spoken_words']:,}"
        )

    endings = [segment for segment in segments if segment.get("kind") == "end_matter"]
    if len(endings) != 1:
        errors.append(f"expected one end marker; found {len(endings)}")

    source_paths = [REPO_ROOT / item for item in manifest.get("source", {}).get("files", [])]
    missing = [str(path) for path in source_paths if not path.is_file()]
    if missing:
        errors.append(f"manifest refers to missing source files: {', '.join(missing)}")
    elif source_paths and source_digest(source_paths) != manifest.get("source", {}).get("sha256"):
        errors.append("the manifest is stale because canonical manuscript source has changed")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_WORK_DIR / "manifest.json")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest)
    config = load_config(args.config)
    errors = validate(manifest, config)
    if errors:
        print("Audiobook manifest validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    stats = manifest["stats"]
    print(
        f"Audiobook manifest valid: {stats['chapters']} chapters, "
        f"{stats['segments']} segments, {stats['spoken_words']:,} spoken words."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

