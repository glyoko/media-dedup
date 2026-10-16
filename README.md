# media-dedup

A command line tool for deduplicating media files.

## What it is

Say you've got a memes folder with 1000 images in it. You downloaded them from disparate sources. Maybe the web, maybe Discord, maybe a friend texted them to you. Maybe some of them have been shared multiple times, and their quality has degraded. Maybe their dimensions were changed, and changed again. Maybe some are watermarked. What this tool does is it finds media that is "similar", and cleans up your folders.

If it's not sure, it'll pause to ask you what to do. If it's positive that two files are the same, it'll remove one of them. If two images are basically the same, but one is of higher quality, it'll keep the higher quality image.

It works for both images and videos.

## Install

Requires Python 3.10+. With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install .          # or, from a checkout: uv run media-dedup ...
uv tool install '.[heic]'  # optional: HEIC/HEIF/AVIF photos (iPhone)
```

or `pip install .`. No system packages are needed; videos are decoded with OpenCV.

## Usage

```sh
media-dedup --dry-run ~/Pictures/memes   # look first
media-dedup ~/Pictures/memes             # then clean up
```

"Removed" files are never deleted. They're moved into `.media-dedup-removed/` inside the
(first) folder you scan, keeping their folder structure, and every move is logged to
`.media-dedup-removed/manifest.jsonl` (original path, where it went, which file was kept).
Delete that folder once you're happy, or put things back with `media-dedup restore`:

```sh
media-dedup restore ~/Pictures/memes                 # put back everything that was removed
media-dedup restore -d ~/Pictures/memes              # list what would be put back
media-dedup restore -o 'cat*' -o 'old/*.mp4' ~/Pictures/memes   # only matching files
```

`DIR` is the folder you scanned or the quarantine folder itself (default: the current
folder). Restored files are taken off the manifest, and the quarantine folder is removed
once it's empty. A file is left in the quarantine, and reported, if something new already
exists at its original path (nothing is ever overwritten) or if it was deleted from the
quarantine. Restoring uses the absolute paths in the manifest, so it can't follow a folder
that was moved or renamed since. To scan a folder that is literally named `restore`, write
`./restore`.

What happens to each kind of duplicate:

| Found | What happens |
|-------|--------------|
| Byte-identical copies | Removed automatically; the original-looking name is kept (`cat.jpg` over `cat (1).jpg`). |
| Same picture/video, different size or compression | Removed automatically; the highest resolution copy is kept (ties: larger file). |
| Probably related, but visibly different (caption, watermark, crop, colour change...) | You're asked. |

When asked, press Enter to keep the suggested file, type numbers (`2` or `1,3`) to keep
those, `a` to keep all of them, `v` to open them in your viewer, or `q` to stop asking.

## Options

| Option | Description |
|--------|-------------|
| `-d`, `--dry-run` | Output what files are duplicates and which would be removed, but don't actually remove anything. |
| `-c`, `--checksum-only` | Only remove byte-identical copies (matched by SHA-256). Much faster: no image/video analysis, and never asks. |
| `-n`, `--non-interactive` | Never ask; leave uncertain groups alone. Automatic when stdin isn't a terminal. |
| `--no-recursive` | Only scan the top level of each folder (subfolders are scanned by default). |
| `-q DIR`, `--quarantine-dir DIR` | Where removed files go. Default: `PATH/.media-dedup-removed`. |
| `--phash N` | Max pHash distance (0–64) for two files to be shown as possible duplicates. Default: 12. Lower it if unrelated pictures get grouped; raise it to catch heavier edits. |
| `--dhash N` | Max dHash distance (0–64) for possible duplicates. Default: 26. Both hashes have to be within their limits. |
| `-j N`, `--jobs N` | Number of parallel decoders. Default: CPU count. |
| `-v`, `--verbose` | List files that couldn't be read. |
| `-D`, `--debug` | After each group, show both hashes of every file and each check against the keeper (distances, local and colour diff, aspect, flat/low-contrast flags), with the rule that decided it. |

`--phash` and `--dhash` only change which files are *shown to you*. What's removed
automatically still needs every strict check to pass (and, since a file has to be a possible
match first, lowering a limit below 6 or 10 also stops some automatic removals). `-D` prints
both distances for every pair, which helps pick values.

Images: JPEG, PNG, GIF (first frame), WebP, BMP, TIFF (+ HEIC/HEIF/AVIF with the `heic` extra).
Videos: MP4, M4V, MOV, MKV, WebM, AVI, WMV, FLV, MPEG, 3GP.
Hidden files/folders and symlinks are skipped.

## Development

```sh
uv sync
uv run pytest   # runs the tests with a coverage report
```

## License

MIT. See [LICENSE](LICENSE). The test images in `tests/data/images` are Unsplash photos under the [Unsplash License](https://unsplash.com/license); see `tests/data/images/SOURCES.md`.
