from __future__ import annotations

import copy
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "audiobook" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audiobook_common import DEFAULT_CONFIG, all_segments, load_config  # noqa: E402
from checkpoint import safe_extract  # noqa: E402
from generate_audio import select_segments  # noqa: E402
from prepare_manuscript import (  # noqa: E402
    apply_pronunciations,
    chunk_text,
    create_manifest,
    discover_sources,
    fingerprint_segment,
    spoken_chapter_heading,
)
from validate_manifest import validate  # noqa: E402


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(DEFAULT_CONFIG)
        cls.manifest = create_manifest(cls.config, "narrator")

    def test_canonical_source_and_chapter_counts(self):
        self.assertEqual(self.manifest["stats"]["source_files"], 46)
        self.assertEqual(self.manifest["stats"]["chapters"], 74)
        self.assertGreater(self.manifest["stats"]["spoken_words"], 65_000)

    def test_opening_and_ending_are_present(self):
        self.assertEqual(
            self.manifest["chapters"][0]["title"],
            "FRIDAY 20 SEPTEMBER 2019 — ARREST",
        )
        self.assertEqual(
            self.manifest["chapters"][-1]["title"],
            "TUESDAY 25 FEBRUARY 2020 — TWO VANS",
        )
        endings = [s for s in all_segments(self.manifest) if s["kind"] == "end_matter"]
        self.assertEqual(len(endings), 1)

    def test_source_sort_places_lettered_insert_correctly(self):
        source_dir = REPO_ROOT / self.config["book"]["source_dir"]
        names = [path.name for path in discover_sources(source_dir)]
        self.assertLess(names.index("21-17-OCTOBER-2019.md"), names.index("21A-19-OCTOBER-2019.md"))
        self.assertLess(names.index("21A-19-OCTOBER-2019.md"), names.index("22-20-OCTOBER-2019.md"))

    def test_manifest_is_deterministic(self):
        again = create_manifest(self.config, "narrator")
        self.assertEqual(self.manifest, again)

    def test_body_chunks_respect_configured_limit(self):
        maximum = self.config["production"]["max_segment_characters"]
        body = [s for s in all_segments(self.manifest) if s["kind"] == "body"]
        self.assertTrue(body)
        self.assertLessEqual(max(len(segment["text"]) for segment in body), maximum)

    def test_date_heading_is_spoken_naturally(self):
        self.assertEqual(
            spoken_chapter_heading("FRIDAY 20 SEPTEMBER 2019 — ARREST"),
            "Friday, the twentieth of September, twenty nineteen. Arrest.",
        )

    def test_only_approved_pronunciations_are_applied(self):
        result = apply_pronunciations("HMP Lewes, Jamo and Macca", self.config)
        self.assertEqual(result, "H M P Loo-iss, Jamo and Macca")

    def test_narrator_and_pov_modes_are_distinct(self):
        narrator_voices = {s["voice"] for s in all_segments(self.manifest)}
        pov_manifest = create_manifest(self.config, "pov")
        pov_voices = {s["voice"] for s in all_segments(pov_manifest)}
        self.assertEqual(narrator_voices, {"bm_george"})
        self.assertGreater(len(pov_voices), 3)
        connor_body = next(
            s for s in all_segments(pov_manifest)
            if s["kind"] == "body" and s["cast_key"] == "CONNOR"
        )
        self.assertEqual(connor_body["voice"], "bm_lewis")

    def test_validator_accepts_fresh_manifest(self):
        self.assertEqual(validate(self.manifest, self.config), [])

    def test_fingerprint_detects_audio_input_change(self):
        segment = copy.deepcopy(next(iter(all_segments(self.manifest))))
        original = segment["fingerprint"]
        segment["speed"] += 0.01
        self.assertNotEqual(original, fingerprint_segment(segment))

    def test_sample_selection_includes_front_matter_and_is_bounded(self):
        selected, label = select_segments(
            self.manifest, self.config, sample=True, chapter_number=None, limit=None
        )
        self.assertEqual(selected[0]["kind"], "front_matter")
        self.assertTrue(all(s["chapter"] in {0, 1} for s in selected))
        self.assertIn("approval sample", label)


class TextChunkTests(unittest.TestCase):
    def test_long_unpunctuated_text_is_split(self):
        chunks = chunk_text("word " * 500, 120)
        self.assertGreater(len(chunks), 1)
        self.assertLessEqual(max(map(len, chunks)), 120)


class CheckpointTests(unittest.TestCase):
    def test_restore_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.txt", "no")
            with self.assertRaises(ValueError):
                safe_extract(archive, root / "destination")


if __name__ == "__main__":
    unittest.main()

