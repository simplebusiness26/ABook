# ABook audiobook studio

This directory turns the canonical Draft V3 manuscript of *Hear All, See All, Say Nothing* into a reviewable audiobook without paid text-to-speech services.

The manuscript remains in `book-01/draft-v3/`. Everything generated from it stays under `audiobook/work/` or `audiobook/exports/`, both of which are deliberately excluded from Git so large audio files never clutter the book repository.

## Current production decision

The first approval sample uses one restrained British male narrator. The system also supports a POV-cast experiment, but it does not pretend that automatic dialogue attribution is reliable. Character dialogue remains part of the narrator's performance unless the manuscript is deliberately annotated later.

The configured engine is Kokoro-82M with its built-in British voice packs. It is small enough for free Kaggle GPU use and does not clone a real person's voice.

## What the pipeline does

1. Reads only canonical numbered Draft V3 manuscript files.
2. Applies the same reader-facing staff-name and meta-text cleanup used by the complete PDF build.
3. Turns 46 source files into 74 correctly ordered dated audio chapters.
4. Announces dated chapter and labelled POV headings in speech-friendly form.
5. Applies approved pronunciations without changing manuscript source.
6. Creates hash-addressed FLAC segments, skipping valid completed segments on reruns.
7. Builds a loudness-normalised MP3 approval sample.
8. For an approved full run, builds individual MP3 chapters and a chapter-marked M4B.
9. Produces structural QC and checkpoint files.

## Fastest route: Kaggle

The ready-made notebook is `notebooks/ABook_Audiobook_Studio.ipynb`.

1. Import that notebook into Kaggle.
2. In **Notebook options**, turn on a GPU accelerator and Internet access.
3. Leave `RUN_MODE = "sample"` and `VOICE_MODE = "narrator"` for the first run.
4. Choose **Run all**.
5. Listen to the exported approval sample before changing the voice configuration or starting the full book.

No API key, payment method or voice sample is required. Model files are downloaded from the public model repository during the first run.

For the complete book, change only:

```python
RUN_MODE = "full"
```

The notebook creates `abook-audiobook-checkpoint.zip`. Saving the Kaggle notebook version preserves its outputs. If a later session needs to resume, attach the prior checkpoint ZIP as notebook input; the notebook finds and restores it automatically. Unchanged segment hashes are skipped.

## Commands

Prepare and validate without downloading a voice model:

```bash
python audiobook/scripts/prepare_manuscript.py --summary
python audiobook/scripts/validate_manifest.py
```

Exercise the complete command flow without generating audio:

```bash
python audiobook/scripts/run_pipeline.py --mode sample --dry-run
```

Generate the narrator approval sample after installing `requirements-kaggle.txt` and `espeak-ng`:

```bash
python audiobook/scripts/run_pipeline.py --mode sample --device cuda
```

Generate a second sample with POV-specific voices:

```bash
python audiobook/scripts/run_pipeline.py --mode sample --voice-mode pov --device cuda
```

After a sample and the outstanding pronunciation decisions are approved:

```bash
python audiobook/scripts/run_pipeline.py --mode full --voice-mode narrator --device cuda
```

## Output layout

```text
audiobook/
├── config/                 Book, voice, pronunciation and mastering decisions
├── notebooks/              Phone-friendly Kaggle runner
├── scripts/                Preparation, generation, mastering, QC and checkpoints
├── tests/                  Fast tests that do not download the voice model
├── work/                   Regenerable manifests and FLAC checkpoints (ignored)
└── exports/                Samples, MP3 chapters, M4B and checkpoint ZIP (ignored)
```

## Approval gate

Do not treat generated audio as a publishing master until all of these are complete:

- The Draft V3 developmental and line edit is locked.
- The main narrator and delivery speed are approved.
- The pronunciation decisions in `config/VOICE_REVIEW.md` are resolved.
- The chosen author name or pen name is added to `book.author` in the configuration.
- Every chapter receives a human listen-through.
- The final files are checked against the current rules of the selected audiobook distributor.
- Third-party licence information is rechecked immediately before commercial release.
