"""Move removed duplicates into a quarantine folder instead of deleting them.

The manifest is the only record of where each file came from, so :func:`restore`
works from it alone and never guesses: an entry whose quarantined file is gone,
or whose original path is occupied again, is reported and left in the manifest.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, Literal, Sequence

from .media import MediaFile

DEFAULT_DIRNAME = ".media-dedup-removed"
MANIFEST_NAME = "manifest.jsonl"


def default_directory(paths: Iterable[Path]) -> Path:
    """``<first scanned folder>/.media-dedup-removed``."""
    first = Path(next(iter(paths)))
    base = first if first.is_dir() else first.parent
    return base / DEFAULT_DIRNAME


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


class Quarantine:
    """Moves files into ``directory``, mirroring their layout under the scan roots.

    Every move is appended to ``manifest.jsonl`` in the quarantine folder, with
    the original location and the file that was kept in its place, so anything
    can be put back by hand.
    """

    def __init__(self, directory: Path, roots: Iterable[Path]):
        self.directory = Path(directory)
        resolved = [str(Path(r).resolve()) for r in roots]
        self.base = Path(os.path.commonpath(resolved)) if resolved else Path("/")
        self.moved: list[tuple[MediaFile, Path]] = []

    @property
    def manifest(self) -> Path:
        return self.directory / MANIFEST_NAME

    def destination_for(self, mf: MediaFile) -> Path:
        source = mf.path.resolve()
        try:
            relative = source.relative_to(self.base)
        except ValueError:  # pragma: no cover - roots always contain their files
            relative = Path(source.name)
        return self.directory / relative

    def move(self, mf: MediaFile, kept: MediaFile, reason: str) -> Path:
        original = mf.path.resolve()
        dest = _unique(self.destination_for(mf))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mf.path), str(dest))
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "original": str(original),
            "quarantined": str(dest.resolve()),
            "kept": str(kept.path.resolve()),
            "reason": reason,
        }
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        self.moved.append((mf, dest))
        return dest

    @property
    def bytes_moved(self) -> int:
        return sum(mf.size for mf, _ in self.moved)


# --- Restoring ---------------------------------------------------------------

RestoreStatus = Literal["restored", "taken", "missing", "failed"]


@dataclass(frozen=True)
class RestoreEntry:
    original: Path
    quarantined: Path
    status: RestoreStatus
    error: str = ""


@dataclass
class RestoreResult:
    entries: list[RestoreEntry]
    unreadable_lines: int = 0

    def with_status(self, status: RestoreStatus) -> list[RestoreEntry]:
        return [e for e in self.entries if e.status == status]


def find_manifest(path: Path) -> Path | None:
    """The manifest for ``path``: a quarantine folder, the folder that was
    scanned (holding the default quarantine folder), or the manifest itself."""
    path = Path(path)
    if path.name == MANIFEST_NAME and path.is_file():
        return path
    for candidate in (path / MANIFEST_NAME, path / DEFAULT_DIRNAME / MANIFEST_NAME):
        if candidate.is_file():
            return candidate
    return None


def _matches(path: Path, patterns: Sequence[str]) -> bool:
    text = str(path)
    return any(fnmatch(path.name, p) or fnmatch(text, p) or fnmatch(text, "*/" + p) for p in patterns)


def restore(manifest: Path, patterns: Sequence[str] = (), dry_run: bool = False) -> RestoreResult:
    """Move quarantined files back to their original paths.

    Only entries whose original or quarantined path matches one of
    ``patterns`` (shell globs, matched against the file name or any trailing
    part of the path) are restored, or all of them if there are none. Restored
    entries are removed from the manifest, and quarantine folders left empty
    are deleted. With ``dry_run`` nothing is touched.
    """
    result = RestoreResult([])
    kept_lines: list[str] = []
    claimed: set[Path] = set()  # originals restored earlier in this run
    emptied: set[Path] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            original, quarantined = Path(record["original"]), Path(record["quarantined"])
        except (ValueError, KeyError, TypeError):
            result.unreadable_lines += 1
            kept_lines.append(line)
            continue
        if patterns and not (_matches(original, patterns) or _matches(quarantined, patterns)):
            kept_lines.append(line)
            continue
        entry = _restore_one(original, quarantined, claimed, dry_run)
        result.entries.append(entry)
        if entry.status == "restored":
            claimed.add(original)
            emptied.add(quarantined.parent)
        else:
            kept_lines.append(line)

    if not dry_run and claimed:
        _rewrite_manifest(manifest, kept_lines)
        _remove_empty_dirs(emptied, manifest.parent)
    return result


def _restore_one(original: Path, quarantined: Path, claimed: set[Path], dry_run: bool) -> RestoreEntry:
    if not quarantined.is_file():
        return RestoreEntry(original, quarantined, "missing")
    if original in claimed or os.path.lexists(original):
        return RestoreEntry(original, quarantined, "taken")
    if not dry_run:
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(quarantined), str(original))
        except OSError as exc:
            return RestoreEntry(original, quarantined, "failed", str(exc))
    return RestoreEntry(original, quarantined, "restored")


def _rewrite_manifest(manifest: Path, lines: list[str]) -> None:
    if not lines:
        manifest.unlink()
        return
    tmp = manifest.with_name(manifest.name + ".tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    os.replace(tmp, manifest)


def _remove_empty_dirs(dirs: Iterable[Path], top: Path) -> None:
    """Delete each of ``dirs`` and its parents up to ``top`` while they're empty."""
    top = top.resolve()
    for d in sorted({d.resolve() for d in dirs}, key=lambda d: len(d.parts), reverse=True):
        while d == top or top in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            if d == top:
                break
            d = d.parent
