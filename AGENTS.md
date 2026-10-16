## What this is

`media-dedup` is a Python CLI that finds duplicate images and videos and quarantines the
lesser copies. The spec is `README.md`. `OPEN_QUESTIONS.md` lists open product questions;
check it before changing behaviour the owner may already have an opinion on.

## Commands

```sh
uv sync                                # install deps + dev group (pytest, pytest-cov)
uv run pytest                          # all tests + coverage report (~8 s)
uv run pytest --no-cov -q tests/test_signature.py   # fast iteration
uv run media-dedup --dry-run PATH      # run the tool
uv run media-dedup -d -D PATH          # dry run + why each group formed
uv run media-dedup restore [-d] PATH   # undo: move quarantined files back
```

- The venv is pinned to **Python 3.13** (`.python-version`). The package supports 3.10+.
- Use `uv` throughout, not pip directly.

## Owner decisions (don't change without asking)

- **Removal = move to a quarantine folder**, never delete. The default is
  `<first PATH>/.media-dedup-removed/`, and every move is logged to `manifest.jsonl`.
- **Video decoding uses OpenCV** (`opencv-python-headless`), not system ffmpeg.
- **Keeper = highest resolution, then largest file.** The other tie-breakers are in
  `quality.py`.
- **Scanning is recursive by default**; `--no-recursive` turns it off.
- `-d/--dry-run` must never modify the filesystem, including creating the quarantine folder.
- **The checksum pass always runs first**, and only the keeper of each identical group is
  decoded afterwards. `-c/--checksum-only` stops after that pass and must never decode
  media; `test_checksum_only_*` enforce this.

## Architecture

`restore` is detected as the first argument in `cli.main` (not an argparse subcommand, so
`media-dedup PATH` still works) and handled by `cli.restore_main`. The pipeline in `cli.main`:

```
scan -> find_exact -> [unless --checksum-only: fingerprint_all -> find_similar]
     -> dry-run report | quarantine + prompt
```

`find_exact` groups files by size and SHA-256s only the files whose size collides.

| Module | Role |
|---|---|
| `media.py` | `MediaFile` dataclass and `scan()`. Hidden files, symlinks and duplicate inodes are skipped. |
| `fingerprint.py` | Decoding and SHA-256. Images use PIL draft mode. Videos get `VIDEO_SAMPLES=10` frames at evenly spaced *times*. |
| `signature.py` | `Signature` (pHash, dHash, 128px gray thumb, 16px colour thumb, dims, flat flag) and `compare() -> Match(level=NONE/POSSIBLE/CERTAIN)`. **All thresholds live here.** |
| `matching.py` | `DuplicateGroup(kind=exact/similar/uncertain)`, candidate search, union-find clustering, and the anti-chaining rule. |
| `quality.py` | `quality_key` / `rank`. |
| `quarantine.py` | `Quarantine.move()`, the manifest, and `restore()` (never overwrites; unrestored entries stay in the manifest). |
| `prompt.py` | `ask()`, which raises `QuitRequested`. |
| `report.py` | Output formatting. |

Notes on the data model:

- `MediaFile.signatures` has one entry for an image and `VIDEO_SAMPLES` entries for a
  video.
- For videos, an entry can be `None` (an undecodable frame). Keep the slots aligned by
  position.

## Matching rules worth knowing

- **CERTAIN requires every check to pass:** small pHash and dHash distances, matching
  aspect ratio, small colour difference, and a small **local diff**. The local diff is the
  max mean |diff| over 4×4 blocks of the blurred 128px grayscale thumbnails.
- **The local-diff check is what stops memes with different captions from being
  auto-removed. Don't weaken it.** Hash distance alone puts them in the same range as a
  resized copy.
- **POSSIBLE needs both hashes close** (`PHASH_POSSIBLE` and `DHASH_POSSIBLE`). pHash
  alone collides for unrelated photos with a similar layout.
- **Solid-colour ("flat") images and frames are never CERTAIN.** Nor are low-contrast
  ones (`LOW_CONTRAST_STDDEV`, e.g. very dark photos): the absolute local-diff limit can't
  see a caption that was darkened along with them.
- **Videos use a stronger blur** (`VIDEO_MIN_BLUR`), because two encodes' sampled frames
  are slightly misaligned in time.
- **Videos must have similar durations** to be compared at all.

## Changing thresholds

Thresholds are calibrated against `tests/data/images` and the synthetic variants in
`tests/media_factory.py`.

- `tests/test_signature.py` is the calibration suite. Benign degradations must stay
  CERTAIN, and edits (captions, watermarks, crops, brightness, mirror) must never be
  CERTAIN.
- After any change to `signature.py` constants, run it and look at the margins, not just
  pass/fail.
- Measured margins: benign local diff is ≤ about 6, watermarks are ≥ about 21, captions
  ≥ about 44.

## Testing conventions and gotchas

- **Test data**: the 14 Unsplash photos in `tests/data/images` (attribution in
  `SOURCES.md`). Generate variants and videos at runtime with `tests/media_factory.py`
  (`load`, `recompress`, `caption`, `watermark`, `crop`, `write_video`). Don't commit
  derived files.
- `tests/` has no `__init__.py`. Pytest's rootdir import mode makes `media_factory`
  importable directly.
- Fixtures in `conftest.py`: `save(img, "rel/name.jpg")` writes under `tmp_path`;
  `make_media(path, fingerprinted=True)` builds a `MediaFile`.
- **Never subclass `cv2.VideoCapture` in tests**; it segfaults the interpreter. Wrap it
  instead (see `test_video_without_frame_count_is_counted`).
- **Prompt input**: `prompt.ask()` resolves `input` at call time, so tests can
  `monkeypatch.setattr("builtins.input", ...)`.
- **Interactive mode in tests**: the CLI checks `cli._stdin_is_interactive()`; monkeypatch
  it to `lambda: True` for interactive end-to-end tests.
- **Coverage**: keep it at ≥ 95% (currently 99%). `__main__.py` only runs in a subprocess,
  so it shows as uncovered.

## Style

- Type hints everywhere, with `from __future__ import annotations`.
- Dataclasses for data.
- Module docstrings explain the *why*; comments are sparse.
- Small pure functions, and side effects kept in `cli.py` and `quarantine.py`.
- Constants are UPPER_CASE at module top, with a `#:` comment explaining the calibration.
