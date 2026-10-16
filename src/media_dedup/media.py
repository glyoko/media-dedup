"""The :class:`MediaFile` model and file discovery."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from .signature import Signature

try:  # Optional HEIC/HEIF/AVIF support: `pip install media-dedup[heic]`.
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HEIF_EXTENSIONS = {".heic", ".heif", ".avif"}
except ImportError:  # pragma: no cover - depends on optional extra
    _HEIF_EXTENSIONS = set()

IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
    | _HEIF_EXTENSIONS
)
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp"}
)

Kind = Literal["image", "video"]


def media_kind(path: Path) -> Kind | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


@dataclass(eq=False)
class MediaFile:
    path: Path
    root: Path  # the scan root this file was found under
    kind: Kind
    size: int
    mtime: float
    # Filled in by fingerprinting:
    width: int = 0
    height: int = 0
    format: str = ""
    duration: float = 0.0  # seconds; videos only
    # One per image; one per sample position for videos (None = undecodable frame).
    signatures: list[Signature | None] = field(default_factory=list)

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def __repr__(self) -> str:
        return f"MediaFile({str(self.path)!r})"


def scan(
    paths: Iterable[Path],
    recursive: bool = True,
    exclude: Iterable[Path] = (),
) -> list[MediaFile]:
    """Find media files under ``paths``.

    Hidden files and directories (dot-names, e.g. macOS ``._foo.jpg`` resource
    forks) and symlinks are skipped, as is anything under ``exclude``. A file
    reachable through several paths or hard links is only returned once, since
    removing one of its names would free nothing.
    """
    excluded = {p.resolve() for p in exclude}
    seen: set[tuple[int, int]] = set()
    found: list[MediaFile] = []

    def consider(path: Path, root: Path) -> None:
        if path.name.startswith(".") or path.is_symlink():
            return
        kind = media_kind(path)
        if kind is None:
            return
        try:
            st = path.stat()
        except OSError:
            return
        key = (st.st_dev, st.st_ino)
        if key in seen:
            return
        seen.add(key)
        found.append(MediaFile(path=path, root=root, kind=kind, size=st.st_size, mtime=st.st_mtime))

    for given in paths:
        given = Path(given)
        if given.is_file():
            consider(given, given.parent)
            continue
        root = given
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            if recursive:
                dirnames[:] = sorted(
                    d
                    for d in dirnames
                    if not d.startswith(".") and (current / d).resolve() not in excluded
                )
            else:
                dirnames[:] = []
            for name in sorted(filenames):
                consider(current / name, root)
    return found
