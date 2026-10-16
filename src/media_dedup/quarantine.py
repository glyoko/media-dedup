"""Move removed duplicates into a quarantine folder instead of deleting them."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
