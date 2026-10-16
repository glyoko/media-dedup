# Open questions

Each question below has a recommended default. Answering one changes the behaviour; leaving
it unanswered keeps the default, which is how the tool currently works.

## Which copy to keep

1. **Re-saves win on file size.** "Resolution, then size" means a PNG re-save of a JPEG
   (612 KB) beats the original JPEG (75 KB), even though they look identical. Upscaled
   copies also beat their sharper originals, because they have more pixels.

   Options:
   - (a) Keep as is.
   - (b) When resolutions match, prefer the *smaller* file if the two are visually identical.
   - (c) Add a sharpness measure (e.g. Laplacian variance) so blurry upscales lose.

   I'd lean toward (c) combined with "prefer the original format/JPEG over a lossless re-save
   of it". Tell me before I change it.

2. **Suggested keeper for memes.** In a "possible duplicates" group, the suggestion uses the
   same quality rule, so a captioned meme often gets suggested over the clean template,
   because it's a larger file. Should the suggestion lean differently (e.g. the oldest
   file), or is it fine since you confirm it anyway?

3. **Grayscale copies.** A grayscale copy of a nearly colourless photo can count as a
   CERTAIN match, so it's auto-removed (or auto-kept, if it's bigger). Colourful images are
   always asked about. Should "one copy has lost its colour" always go to the prompt?

## Scope

4. **Mirrored and rotated copies.** These aren't detected: a flipped meme or a 90°-rotated
   photo without EXIF orientation counts as unrelated. Should I add them (as "possible", for
   you to decide)?

5. **Trimmed videos and cross-format matches.**
   - Videos must be about the same length (within max(1 s, 5%)), so a trimmed clip isn't
     matched to the full one.
   - A GIF made from a video isn't matched to the video either.
   - Animated GIFs are compared by their first frame only.

   Are any of these important for your use?

6. **More formats.** HEIC/AVIF need the optional `pillow-heif` extra. Should it be a default
   dependency, given that iPhones send HEIC?

## Workflow

7. **Restore and cleanup commands.** The quarantine has a manifest but no
   `media-dedup restore` or `media-dedup empty-quarantine` command. Want those?

8. **Multiple folders.** With `media-dedup A B`, removed files from both folders go to
   `A/.media-dedup-removed/`. Would you rather each folder get its own quarantine?

9. **Caching.** Every run re-decodes everything (about 1 s per 600 images here). For very large
   libraries, should fingerprints be cached between runs, e.g. in a small SQLite file in
   the quarantine folder?

10. **Thresholds as options.** Thresholds are constants in `signature.py`, calibrated
    conservatively. Do you want a `--strict` / `--loose` switch, or are fixed thresholds
    fine?

