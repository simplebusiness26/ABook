# Audiobook voice review

This file is the decision sheet for *Hear All, See All, Say Nothing*. Do not lock a full-book run until the approval sample has been listened to on both headphones and a phone speaker.

## Locked production principles

- British English only for the first edition.
- Natural delivery; no exaggerated accents or comic impressions.
- The default edition uses one consistent narrator.
- The optional POV edition changes voice at labelled POV sections. It does not attempt unreliable automatic dialogue-speaker detection.
- Craig remains external in Book One. His configured voice is used only where the manuscript explicitly labels an external Craig section.
- Generated audio is a review master until a complete human listen-through and platform-specific loudness check are complete.

## Decisions still requiring a listen

| Decision | Current working choice | Status |
| --- | --- | --- |
| Main narrator | `bm_george`, speed `0.96` | Test in approval sample |
| Alternative narrator | `bm_fable`, speed `0.95` | Audition if needed |
| Edition style | Single narrator | Compare with POV sample |
| Author credit | Blank | Add the chosen real name or pen name before full export |
| Jamo pronunciation | `Jam-oh` | Author confirmation needed |
| Macca pronunciation | `Mack-uh` | Author confirmation needed |
| Lewes pronunciation | `Loo-iss` | Check in a later prison sample |

When a decision is approved, update `audiobook.toml` so every later run uses the same setting.
