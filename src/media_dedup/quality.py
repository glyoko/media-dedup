"""Decide which of several duplicate files is the best one to keep."""

from __future__ import annotations

import re
from typing import Iterable

from .media import MediaFile

#: Formats that never lose detail on save. Only used as a tie-breaker.
LOSSLESS_FORMATS = frozenset({"PNG", "BMP", "TIFF", "TIF"})

# "cat (1).jpg", "cat copy.jpg", "cat copy 2.jpg", "cat-copy.jpg", "cat_1.jpg"...
_COPY_MARKER = re.compile(r"(\s\(\d+\)|[\s_-]copy(\s?\d+)?|[\s_-]\d)$", re.IGNORECASE)


def looks_like_copy(mf: MediaFile) -> bool:
    return bool(_COPY_MARKER.search(mf.path.stem))


def quality_key(mf: MediaFile) -> tuple:
    """Sort key: higher means better.

    Resolution wins, then file size (more bytes at the same resolution usually
    means less compression). Remaining ties -- e.g. byte-identical copies -- go
    to a lossless format, then a name that doesn't look like a copy, then the
    older file (more likely the original), then the shorter path.
    """
    return (
        mf.pixels,
        mf.size,
        mf.format.upper() in LOSSLESS_FORMATS,
        not looks_like_copy(mf),
        -mf.mtime,
        -len(str(mf.path)),
        str(mf.path),  # final deterministic tie-break
    )


def rank(files: Iterable[MediaFile]) -> list[MediaFile]:
    """Return ``files`` best-first."""
    return sorted(files, key=quality_key, reverse=True)
