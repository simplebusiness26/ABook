#!/usr/bin/env python3
"""Turn the canonical Draft V3 Markdown into a deterministic TTS manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from audiobook_common import (
    AUDIOBOOK_DIR,
    DEFAULT_CONFIG,
    DEFAULT_WORK_DIR,
    REPO_ROOT,
    atomic_write_json,
    load_config,
    relative_to_repo,
)


SOURCE_NAME = re.compile(r"^(?P<number>\d{2})(?P<suffix>[A-Z]?)-.+\.md$")
DATE_HEADING = re.compile(
    r"^(?P<day>[A-Z]+)\s+(?P<date>\d{1,2})\s+(?P<month>[A-Z]+)\s+"
    r"(?P<year>\d{4})\s+[—-]\s+(?P<label>.+)$"
)
SENTENCE = re.compile(r".+?(?:[.!?…]+[\"'’”]*|$)(?:\s+|$)", re.DOTALL)

ORDINALS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
    11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
    15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
    19: "nineteenth", 20: "twentieth", 21: "twenty-first",
    22: "twenty-second", 23: "twenty-third", 24: "twenty-fourth",
    25: "twenty-fifth", 26: "twenty-sixth", 27: "twenty-seventh",
    28: "twenty-eighth", 29: "twenty-ninth", 30: "thirtieth",
    31: "thirty-first",
}

YEAR_WORDS = {
    "2019": "twenty nineteen",
    "2020": "twenty twenty",
}


def discover_sources(source_dir: Path, pattern: str = "*.md") -> list[Path]:
    sources: list[tuple[int, str, str, Path]] = []
    for path in source_dir.glob(pattern):
        match = SOURCE_NAME.match(path.name)
        if not match:
            continue
        number = int(match.group("number"))
        if number < 1:
            continue
        sources.append((number, match.group("suffix"), path.name, path))
    sources.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in sources]


def source_number(path: Path) -> int:
    match = SOURCE_NAME.match(path.name)
    if not match:
        raise ValueError(f"Not a manuscript source file: {path}")
    return int(match.group("number"))


def reader_cleanup(text: str, sequence: int) -> str:
    """Match the reader-facing fixes already used by the manuscript PDF build."""
    if sequence <= 41:
        text = text.replace("Officer Leah Mercer", "Officer Mercer")
        text = text.replace("Leah Mercer", "Mercer")
        text = re.sub(r"\bLeah\b", "Mercer", text)
        text = text.replace("Officer Tom Bennett", "Officer Bennett")
        text = text.replace("Tom Bennett", "Officer Bennett")
        text = re.sub(r"\bTom\b", "Bennett", text)

    heading_replacements = {
        "LEAH": "OFFICER MERCER",
        "MERCER": "OFFICER MERCER",
        "TOM BENNETT": "OFFICER BENNETT",
        "BENNETT": "OFFICER BENNETT",
        "GRANT": "OFFICER GRANT",
        "PATEL": "SENIOR OFFICER PATEL",
    }
    for old, new in heading_replacements.items():
        text = re.sub(rf"^##\s+{re.escape(old)}(?=\s|$)", f"## {new}", text, flags=re.M)

    text = text.replace("Officer Officer Mercer", "Officer Mercer")
    text = text.replace("Officer Officer Bennett", "Officer Bennett")
    text = text.replace('"Book Two."', '"Ask me another time."')
    text = text.replace(
        "Book One still did not explain why.",
        "The truth remained somewhere beyond Lewes.",
    )
    text = text.replace(
        "He did not receive a POV.\n\nNot yet.",
        "No one in Lewes could see what he was thinking as the vehicle carried him away.",
    )
    text = text.replace("## CRAIG — SEEN FROM OUTSIDE", "## THE WING — CRAIG'S DEPARTURE")
    text = text.replace("## CRAIG - SEEN FROM OUTSIDE", "## THE WING - CRAIG'S DEPARTURE")
    return text


def markdown_to_speech(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("__", "")
    text = text.replace("*", "").replace("`", "")
    text = re.sub(r"^[-+>]\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def apply_pronunciations(text: str, config: dict[str, Any]) -> str:
    for entry in config.get("pronunciations", []):
        if not entry.get("approved", False):
            continue
        written = entry["written"]
        spoken = entry["spoken"]
        pattern = rf"(?<![\w’'-]){re.escape(written)}(?![\w’'-])"
        text = re.sub(pattern, spoken, text, flags=re.IGNORECASE)
    return text


def title_case_heading(value: str) -> str:
    words = value.lower().split()
    small = {"and", "of", "the", "to", "from", "through"}
    rendered = []
    for index, word in enumerate(words):
        if len(word) == 1 and word.isalpha():
            rendered.append(word.upper())
        elif word in small and index:
            rendered.append(word)
        else:
            rendered.append(word[:1].upper() + word[1:])
    return " ".join(rendered)


def spoken_chapter_heading(heading: str) -> str:
    match = DATE_HEADING.match(heading.strip())
    if not match:
        return title_case_heading(heading.replace("—", ". ").replace("/", ". ")) + "."
    year = match.group("year")
    year_words = YEAR_WORDS.get(year, year)
    label = title_case_heading(match.group("label"))
    return (
        f"{title_case_heading(match.group('day'))}, the "
        f"{ORDINALS[int(match.group('date'))]} of "
        f"{title_case_heading(match.group('month'))}, {year_words}. {label}."
    )


def spoken_section_heading(heading: str) -> str:
    value = re.sub(r"\s+[—-]\s+", ". ", heading.strip())
    value = re.sub(r"\s*/\s*", ". ", value)
    return title_case_heading(value).rstrip(".") + "."


def split_long_piece(text: str, maximum: int) -> list[str]:
    if len(text) <= maximum:
        return [text]
    result: list[str] = []
    remaining = text
    while len(remaining) > maximum:
        cut = max(
            remaining.rfind(", ", 0, maximum + 1),
            remaining.rfind("; ", 0, maximum + 1),
            remaining.rfind(" ", 0, maximum + 1),
        )
        if cut < maximum // 2:
            cut = maximum
        result.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        result.append(remaining)
    return result


def chunk_text(text: str, maximum: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= maximum:
        return [text]

    sentences = [match.group(0).strip() for match in SENTENCE.finditer(text)]
    if not sentences:
        return split_long_piece(text, maximum)

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        pieces = split_long_piece(sentence, maximum)
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > maximum:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def normalise_cast_key(value: str) -> str:
    value = value.upper().replace("-", "—")
    value = re.split(r"\s+—\s+", value, maxsplit=1)[0]
    return re.sub(r"\s+", " ", value).strip()


def cast_profile(heading: str, config: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    key = normalise_cast_key(heading)
    aliases = config.get("aliases", {})
    key = aliases.get(key, key)
    cast = config.get("cast", {})
    if key in cast:
        return key, cast[key]
    # Combined headings deliberately use the neutral narrator unless explicitly configured.
    return None, config["voices"]["default"]


def fingerprint_segment(segment: dict[str, Any]) -> str:
    audio_inputs = {
        "text": segment["text"],
        "voice": segment["voice"],
        "speed": segment["speed"],
        "pause_after_ms": segment["pause_after_ms"],
        "engine": segment["engine"],
        "engine_version": segment["engine_version"],
        "model": segment["model"],
        "language_code": segment["language_code"],
        "sample_rate": segment["sample_rate"],
    }
    serialised = json.dumps(audio_inputs, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def source_digest(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(relative_to_repo(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def create_manifest(config: dict[str, Any], voice_mode: str | None = None) -> dict[str, Any]:
    book = config["book"]
    production = config["production"]
    engine = config["engine"]
    selected_mode = voice_mode or production["voice_mode"]
    if selected_mode not in {"narrator", "pov"}:
        raise ValueError("voice mode must be 'narrator' or 'pov'")

    source_dir = REPO_ROOT / book["source_dir"]
    paths = discover_sources(source_dir, book["source_glob"])
    if not paths:
        raise FileNotFoundError(f"No canonical manuscript files found in {source_dir}")

    default_voice = config["voices"]["default"]
    chapter_voice = config["voices"]["chapter_heading"]
    section_voice = config["voices"]["section_heading"]
    sample_rate = int(engine["sample_rate"])
    max_chars = int(production["max_segment_characters"])

    front_text = f"{book['title']}. {book['subtitle']}."
    if book.get("author"):
        front_text += f" Written by {book['author']}."
    front_segment = {
        "id": "F0001",
        "chapter": 0,
        "kind": "front_matter",
        "display_text": front_text,
        "text": apply_pronunciations(front_text, config),
        "pov": None,
        "cast_key": None,
        "voice": chapter_voice["voice"],
        "speed": float(chapter_voice["speed"]),
        "pause_after_ms": int(production["chapter_pause_ms"]),
        "engine": engine["name"],
        "engine_version": engine["package_version"],
        "model": engine["model"],
        "language_code": engine["language_code"],
        "sample_rate": sample_rate,
        "source_file": None,
    }
    front_segment["fingerprint"] = fingerprint_segment(front_segment)

    chapters: list[dict[str, Any]] = []
    current_chapter: dict[str, Any] | None = None
    current_pov: str | None = None
    paragraph_lines: list[str] = []
    segment_index = 0

    def add_segment(
        *, kind: str, display_text: str, source_file: Path, pause_ms: int,
        profile: dict[str, Any], cast_key: str | None = None,
    ) -> None:
        nonlocal segment_index
        if current_chapter is None:
            raise ValueError(f"Content appears before a dated chapter in {source_file}")
        clean_text = apply_pronunciations(markdown_to_speech(display_text), config)
        if not clean_text:
            return
        segment_index += 1
        segment = {
            "id": f"C{current_chapter['number']:03d}-S{segment_index:04d}",
            "chapter": current_chapter["number"],
            "kind": kind,
            "display_text": markdown_to_speech(display_text),
            "text": clean_text,
            "pov": current_pov,
            "cast_key": cast_key,
            "voice": profile["voice"],
            "speed": float(profile["speed"]),
            "pause_after_ms": int(pause_ms),
            "engine": engine["name"],
            "engine_version": engine["package_version"],
            "model": engine["model"],
            "language_code": engine["language_code"],
            "sample_rate": sample_rate,
            "source_file": relative_to_repo(source_file),
        }
        segment["fingerprint"] = fingerprint_segment(segment)
        current_chapter["segments"].append(segment)

    def flush_paragraph(source_file: Path) -> None:
        nonlocal paragraph_lines
        raw = " ".join(line.strip() for line in paragraph_lines).strip()
        paragraph_lines = []
        if not raw:
            return
        cast_key, pov_profile = cast_profile(current_pov or "", config)
        profile = pov_profile if selected_mode == "pov" and cast_key else default_voice
        pieces = chunk_text(markdown_to_speech(raw), max_chars)
        for index, piece in enumerate(pieces):
            pause = int(production["paragraph_pause_ms"]) if index == len(pieces) - 1 else 120
            resolved_cast = cast_key if selected_mode == "pov" else None
            clean_display = markdown_to_speech(piece)
            clean_text = apply_pronunciations(clean_display, config)
            previous = current_chapter["segments"][-1] if current_chapter else None
            separator = "\n" if index == 0 else " "
            can_merge = bool(
                previous
                and previous["kind"] == "body"
                and previous["voice"] == profile["voice"]
                and previous["speed"] == float(profile["speed"])
                and previous["pov"] == current_pov
                and previous["source_file"] == relative_to_repo(source_file)
                and len(previous["text"]) + len(separator) + len(clean_text) <= max_chars
            )
            if can_merge:
                previous["display_text"] += separator + clean_display
                previous["text"] += separator + clean_text
                previous["pause_after_ms"] = pause
                if index == 0:
                    previous["paragraphs"] = previous.get("paragraphs", 1) + 1
                previous["fingerprint"] = fingerprint_segment(previous)
            else:
                add_segment(
                    kind="body",
                    display_text=piece,
                    source_file=source_file,
                    pause_ms=pause,
                    profile=profile,
                    cast_key=resolved_cast,
                )
                current_chapter["segments"][-1]["paragraphs"] = 1
                current_chapter["segments"][-1]["fingerprint"] = fingerprint_segment(
                    current_chapter["segments"][-1]
                )

    def extend_previous_pause(milliseconds: int) -> None:
        if current_chapter and current_chapter["segments"]:
            previous = current_chapter["segments"][-1]
            previous["pause_after_ms"] = max(previous["pause_after_ms"], int(milliseconds))
            previous["fingerprint"] = fingerprint_segment(previous)

    for path in paths:
        cleaned = reader_cleanup(path.read_text(encoding="utf-8"), source_number(path))
        for raw_line in cleaned.splitlines() + [""]:
            line = raw_line.strip()
            if line.startswith("# "):
                flush_paragraph(path)
                heading = line[2:].strip()
                if heading.upper() == "END OF BOOK ONE":
                    if current_chapter is not None:
                        add_segment(
                            kind="end_matter",
                            display_text="End of Book One.",
                            source_file=path,
                            pause_ms=int(production["chapter_pause_ms"]),
                            profile=chapter_voice,
                        )
                    continue
                extend_previous_pause(int(production["chapter_pause_ms"]))
                current_pov = None
                segment_index = 0
                current_chapter = {
                    "number": len(chapters) + 1,
                    "title": heading,
                    "spoken_title": spoken_chapter_heading(heading),
                    "source_files": [relative_to_repo(path)],
                    "segments": [],
                }
                chapters.append(current_chapter)
                add_segment(
                    kind="chapter_heading",
                    display_text=current_chapter["spoken_title"],
                    source_file=path,
                    pause_ms=int(production["section_pause_ms"]),
                    profile=chapter_voice,
                )
            elif line.startswith("## "):
                flush_paragraph(path)
                extend_previous_pause(int(production["section_pause_ms"]))
                heading = line[3:].strip()
                current_pov = heading
                cast_key, _ = cast_profile(heading, config)
                add_segment(
                    kind="section_heading",
                    display_text=spoken_section_heading(heading),
                    source_file=path,
                    pause_ms=int(production["section_pause_ms"]),
                    profile=section_voice,
                    cast_key=cast_key,
                )
            elif line == "---":
                flush_paragraph(path)
                extend_previous_pause(int(production["scene_break_pause_ms"]))
            elif not line:
                flush_paragraph(path)
            else:
                if current_chapter and relative_to_repo(path) not in current_chapter["source_files"]:
                    current_chapter["source_files"].append(relative_to_repo(path))
                paragraph_lines.append(raw_line)

    if not chapters:
        raise ValueError("The canonical manuscript did not contain any dated chapter headings")

    segments = [front_segment] + [s for chapter in chapters for s in chapter["segments"]]
    manifest = {
        "schema_version": 1,
        "book": dict(book),
        "source": {
            "directory": book["source_dir"],
            "files": [relative_to_repo(path) for path in paths],
            "sha256": source_digest(paths),
        },
        "production": {
            "voice_mode": selected_mode,
            "max_segment_characters": max_chars,
            "engine": engine["name"],
            "engine_version": engine["package_version"],
            "model": engine["model"],
            "language_code": engine["language_code"],
            "sample_rate": sample_rate,
        },
        "front_matter": [front_segment],
        "chapters": chapters,
        "stats": {
            "source_files": len(paths),
            "chapters": len(chapters),
            "segments": len(segments),
            "spoken_words": sum(len(s["text"].split()) for s in segments),
            "spoken_characters": sum(len(s["text"]) for s in segments),
            "voices_used": sorted({s["voice"] for s in segments}),
        },
    }
    return manifest


def narration_script(manifest: dict[str, Any]) -> str:
    lines = [manifest["book"]["title"], manifest["book"]["edition"], ""]
    for chapter in manifest["chapters"]:
        lines.extend([f"CHAPTER {chapter['number']:02d}", chapter["title"], ""])
        for segment in chapter["segments"]:
            if segment["kind"] == "chapter_heading":
                continue
            if segment["kind"] == "section_heading":
                lines.append(f"[{segment['display_text']}]")
            else:
                lines.append(segment["display_text"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_WORK_DIR / "manifest.json")
    parser.add_argument(
        "--script-output", type=Path, default=DEFAULT_WORK_DIR / "narration-script.txt"
    )
    parser.add_argument("--voice-mode", choices=("narrator", "pov"))
    parser.add_argument("--summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    manifest = create_manifest(config, args.voice_mode)
    atomic_write_json(args.output, manifest)
    args.script_output.parent.mkdir(parents=True, exist_ok=True)
    args.script_output.write_text(narration_script(manifest), encoding="utf-8")
    stats = manifest["stats"]
    print(
        f"Prepared {stats['chapters']} chapters and {stats['segments']} segments "
        f"({stats['spoken_words']:,} spoken words) in {manifest['production']['voice_mode']} mode."
    )
    print(f"Manifest: {relative_to_repo(args.output)}")
    if args.summary:
        print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
